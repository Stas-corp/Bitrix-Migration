import logging
import re
from datetime import date, datetime, time

from ..normalizers.dto import BitrixMeeting
from ..normalizers.bitrix_markup import normalize_bitrix_markup, build_employee_name_map
from .base import BaseLoader

_logger = logging.getLogger(__name__)


_FREQ_MAP = {
    'DAILY': 'daily',
    'WEEKLY': 'weekly',
    'MONTHLY': 'monthly',
    'YEARLY': 'yearly',
}
_BYDAY_FIELD_MAP = {
    'MO': 'mon', 'TU': 'tue', 'WE': 'wed', 'TH': 'thu',
    'FR': 'fri', 'SA': 'sat', 'SU': 'sun',
}
_ODOO_BYDAY_VALUES = {'1', '2', '3', '4', '-1'}

_RECURRENCE_FIELDS = (
    'recurrency', 'rrule_type', 'interval', 'end_type', 'count', 'until',
    'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
    'month_by', 'day', 'byday', 'weekday',
)

# Fields safe to re-sync on re-import. Recurrence params are intentionally
# excluded — Odoo's calendar.event.write rejects partial recurrence updates
# unless recurrence_update='all_events' is set on the right base event, which
# is brittle after detach. Recurrence is established only at first create.
_REWRITE_FIELDS = ('name', 'description', 'start', 'stop')


def _parse_bitrix_until(raw):
    """Parse the UNTIL component of a Bitrix RRULE into a ``date``.

    Bitrix usually writes ``DD.MM.YYYY``; some exports use iCal-style
    ``YYYYMMDD[THHMMSSZ]`` or ISO ``YYYY-MM-DD``.
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = re.match(r'^(\d{2})\.(\d{2})\.(\d{4})$', s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.match(r'^(\d{4})(\d{2})(\d{2})', s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _parse_bitrix_exdate(raw):
    """Parse Bitrix EXDATE into a list of ``date``.

    Bitrix stores EXDATE as comma-separated dates (``DD.MM.YYYY``), optionally
    followed by a time component which we discard — Odoo recurrence exception
    tracking works at day granularity.
    """
    if not raw:
        return []
    out = []
    for chunk in str(raw).split(','):
        token = chunk.strip().split()[0] if chunk.strip() else ''
        d = _parse_bitrix_until(token)
        if d and d not in out:
            out.append(d)
    return out


def _bitrix_rrule_to_odoo_recurrence(rrule_str, dtstart=None):
    """Convert a Bitrix RRULE string into Odoo ``calendar.event`` field vals.

    Returns ``None`` for empty / unparseable input. EXDATE is handled
    separately by ``_parse_bitrix_exdate``.
    """
    if not rrule_str:
        return None

    parts = {}
    for item in rrule_str.split(';'):
        if '=' not in item:
            continue
        k, v = item.split('=', 1)
        parts[k.strip().upper()] = v.strip()

    freq = parts.get('FREQ', '').upper()
    if freq not in _FREQ_MAP:
        return None

    try:
        interval = int(parts.get('INTERVAL', '1') or '1')
    except ValueError:
        interval = 1

    vals = {
        'rrule_type': _FREQ_MAP[freq],
        'interval': max(interval, 1),
    }

    count_raw = parts.get('COUNT')
    until_date = _parse_bitrix_until(parts.get('UNTIL'))
    if count_raw and count_raw.isdigit():
        vals['end_type'] = 'count'
        vals['count'] = int(count_raw)
    elif until_date:
        vals['end_type'] = 'end_date'
        vals['until'] = until_date
    else:
        vals['end_type'] = 'forever'

    byday_raw = parts.get('BYDAY', '')
    byday_codes = [d.strip().upper() for d in byday_raw.split(',') if d.strip()]

    if freq == 'WEEKLY':
        any_day = False
        for code, field in _BYDAY_FIELD_MAP.items():
            if code in byday_codes:
                vals[field] = True
                any_day = True
        if not any_day and dtstart is not None:
            ordered = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
            vals[ordered[dtstart.weekday()]] = True

    if freq == 'MONTHLY':
        bymonthday = parts.get('BYMONTHDAY')
        if bymonthday and bymonthday.lstrip('-').isdigit():
            vals['month_by'] = 'date'
            vals['day'] = int(bymonthday)
        elif byday_codes:
            vals['month_by'] = 'day'
            first = byday_codes[0]
            m = re.match(r'^(-?\d+)?([A-Z]{2})$', first)
            if m:
                pos = m.group(1) or '1'
                day_code = m.group(2)
                if pos in _ODOO_BYDAY_VALUES:
                    vals['byday'] = pos
                if day_code in _BYDAY_FIELD_MAP:
                    vals['weekday'] = day_code
        elif dtstart is not None:
            vals['month_by'] = 'date'
            vals['day'] = dtstart.day

    return vals


class MeetingLoader(BaseLoader):
    """Loads Bitrix meetings into calendar.event."""

    entity_type = 'meeting'
    batch_size = 200

    def run(self):
        self.log('Extracting Bitrix meetings...')
        raw = self.extractor.get_meetings()
        self.log(f'Found {len(raw)} meetings')

        if not raw:
            return

        employee_map = self.get_mapping().get_all_mappings('employee')
        employee_name_map = build_employee_name_map(self.env)

        processed = 0
        for batch in self._batched(raw, self.batch_size):
            for row in batch:
                meeting = BitrixMeeting(**row)
                bid = str(meeting.external_id)

                partner_ids = []
                if meeting.participant_bitrix_ids:
                    for uid_str in meeting.participant_bitrix_ids.split(','):
                        uid_str = uid_str.strip()
                        if not uid_str:
                            continue
                        employee = self.find_employee_by_bitrix_id(
                            uid_str, employee_map=employee_map,
                        )
                        if employee:
                            partner = self.get_partner_from_employee(employee)
                            if partner and partner.id not in partner_ids:
                                partner_ids.append(partner.id)

                organizer_partner_id = False
                if meeting.organizer_bitrix_id:
                    organizer_emp = self.find_employee_by_bitrix_id(
                        str(meeting.organizer_bitrix_id), employee_map=employee_map,
                    )
                    if organizer_emp:
                        organizer_partner = self.get_partner_from_employee(organizer_emp)
                        if organizer_partner:
                            organizer_partner_id = organizer_partner.id

                all_partner_ids = set(partner_ids)
                if organizer_partner_id:
                    all_partner_ids.add(organizer_partner_id)

                vals = {
                    'name': meeting.name,
                    'x_bitrix_id': bid,
                    'description': normalize_bitrix_markup(
                        meeting.description or '', employee_name_map,
                    ),
                }
                if meeting.date_start:
                    vals['start'] = meeting.date_start
                if meeting.date_end:
                    vals['stop'] = meeting.date_end
                elif meeting.date_start:
                    vals['stop'] = meeting.date_start

                recurrence_vals = _bitrix_rrule_to_odoo_recurrence(
                    meeting.rrule, meeting.date_start,
                )
                if recurrence_vals:
                    vals['recurrency'] = True
                    vals.update(recurrence_vals)

                if all_partner_ids:
                    vals['partner_ids'] = [(6, 0, sorted(all_partner_ids))]

                record, created = self.get_or_create(
                    'calendar.event',
                    [('x_bitrix_id', '=', bid)],
                    vals,
                    bitrix_id=meeting.external_id,
                    entity_type='meeting',
                )

                if record and not self.dry_run and not created:
                    diff = {}
                    if all_partner_ids:
                        existing_partner_ids = set(record.partner_ids.ids)
                        if existing_partner_ids != all_partner_ids:
                            diff['partner_ids'] = [(6, 0, sorted(all_partner_ids))]

                    for field in _REWRITE_FIELDS:
                        if field not in vals:
                            continue
                        current = record[field]
                        target = vals[field]
                        if hasattr(current, 'id'):
                            current = current.id
                        if current != target:
                            diff[field] = target

                    recurrence_keys = self.env['calendar.event']._get_recurrent_fields()
                    diff = {k: v for k, v in diff.items() if k not in recurrence_keys}

                    if diff:
                        record.with_context(
                            no_mail_notification=True,
                            mail_create_nolog=True,
                            mail_create_nosubscribe=True,
                            tracking_disable=True,
                        ).write(diff)

                if record and not self.dry_run and recurrence_vals:
                    rec_id = self._resolve_recurrence_id(record)
                    if rec_id:
                        # x_bitrix_id is copy=False, so occurrences expanded by
                        # calendar.recurrence._apply_recurrence() (via copy_data)
                        # do not inherit it. Propagate explicitly so purge and
                        # idempotent re-import can find the whole series.
                        self.env.cr.execute(
                            "UPDATE calendar_event SET x_bitrix_id = %s "
                            "WHERE recurrence_id = %s "
                            "  AND (x_bitrix_id IS NULL OR x_bitrix_id = '')",
                            (bid, rec_id),
                        )

                        exdates = _parse_bitrix_exdate(meeting.exdate)
                        if exdates:
                            self._drop_recurrence_exceptions(rec_id, exdates)

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=meeting.external_id)

        self.log_stats()

    def _resolve_recurrence_id(self, record):
        """Find the calendar.recurrence linked to ``record``, even after detach.

        When the imported event's ``start`` does not match the RRULE BYDAY
        (e.g. Bitrix master starts on Tuesday but RRULE=BYDAY=MO), Odoo's
        ``_apply_recurrence`` detaches the original event: ``recurrence_id``
        is cleared and ``active`` becomes False. The fresh recurrence picks
        one of the generated Monday-occurrences as ``base_event_id``. In
        that case ``record.recurrence_id`` is empty even though a recurrence
        belonging to this meeting does exist — find it by matching the
        recently created recurrence whose base event shares ``record``'s name.
        """
        record.invalidate_recordset(['recurrence_id', 'active'])
        if record.recurrence_id:
            return record.recurrence_id.id
        if record.active:
            return None
        Recurrence = self.env['calendar.recurrence'].sudo()
        candidate = Recurrence.search(
            [('create_date', '>=', record.create_date),
             ('base_event_id.name', '=', record.name)],
            order='id desc',
            limit=1,
        )
        return candidate.id if candidate else None

    def _drop_recurrence_exceptions(self, recurrence_id, exdates):
        """Archive occurrences of ``recurrence_id`` whose start.date() is in EXDATE.

        Odoo treats ``active=False`` on a recurrence occurrence as an exception
        (the slot stays excluded even after _apply_recurrence reruns).
        """
        if not exdates or not recurrence_id:
            return

        start_min = datetime.combine(min(exdates), time.min)
        stop_max = datetime.combine(max(exdates), time.max)

        Event = self.env['calendar.event'].sudo().with_context(active_test=False)
        candidates = Event.search([
            ('recurrence_id', '=', recurrence_id),
            ('start', '>=', start_min),
            ('start', '<=', stop_max),
        ])
        targets = candidates.filtered(lambda e: e.start.date() in exdates)
        if targets:
            targets.write({'active': False})
