import logging

from ..normalizers.dto import BitrixMeeting
from ..normalizers.bitrix_markup import normalize_bitrix_markup, build_employee_name_map
from .base import BaseLoader

_logger = logging.getLogger(__name__)


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

                # Resolve participants
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

                # Resolve organizer
                organizer_partner_id = False
                if meeting.organizer_bitrix_id:
                    organizer_emp = self.find_employee_by_bitrix_id(
                        str(meeting.organizer_bitrix_id), employee_map=employee_map,
                    )
                    if organizer_emp:
                        organizer_partner = self.get_partner_from_employee(organizer_emp)
                        if organizer_partner:
                            organizer_partner_id = organizer_partner.id

                # Merge participants + organizer into a single set
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

                # Include partner_ids in initial vals so create() handles them
                # with the migration context (tracking_disable=True etc.) — avoids
                # a separate write() that would trigger _notify_attendees().
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
                    # Existing record — sync attendees with notifications suppressed
                    if all_partner_ids:
                        existing_partner_ids = set(record.partner_ids.ids)
                        if existing_partner_ids != all_partner_ids:
                            record.with_context(
                                no_mail_notification=True,
                                mail_create_nolog=True,
                                mail_create_nosubscribe=True,
                                tracking_disable=True,
                            ).write({
                                'partner_ids': [(6, 0, sorted(all_partner_ids))],
                            })

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=meeting.external_id)

        self.log_stats()
