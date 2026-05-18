from datetime import date, datetime

from odoo.tests.common import TransactionCase


class TestMeetingDTO(TransactionCase):
    """Tests for BitrixMeeting DTO (2.11)."""

    def test_dto_basic(self):
        from ..services.normalizers.dto import BitrixMeeting
        m = BitrixMeeting(
            external_id=42,
            name='Sprint Review',
            date_start='2024-01-15 10:00:00',
            date_end='2024-01-15 11:00:00',
            participant_bitrix_ids='1, 2, 3',
            organizer_bitrix_id=1,
            description='Review sprint results',
            forum_topic_id='123',
            rrule='FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO',
            exdate='29.12.2025',
            section_id='17',
        )
        self.assertEqual(m.external_id, 42)
        self.assertEqual(m.name, 'Sprint Review')
        self.assertIsNotNone(m.date_start)
        self.assertIsNotNone(m.date_end)
        self.assertEqual(m.participant_bitrix_ids, '1, 2, 3')
        self.assertEqual(m.organizer_bitrix_id, 1)
        self.assertEqual(m.forum_topic_id, 123)
        self.assertEqual(m.rrule, 'FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO')
        self.assertEqual(m.exdate, '29.12.2025')
        self.assertEqual(m.section_id, 17)

    def test_dto_minimal(self):
        from ..services.normalizers.dto import BitrixMeeting
        m = BitrixMeeting(external_id=1, name='Standup')
        self.assertEqual(m.name, 'Standup')
        self.assertIsNone(m.date_start)
        self.assertIsNone(m.date_end)
        self.assertIsNone(m.participant_bitrix_ids)
        self.assertIsNone(m.organizer_bitrix_id)

    def test_dto_empty_name(self):
        from ..services.normalizers.dto import BitrixMeeting
        m = BitrixMeeting(external_id=1, name='')
        self.assertEqual(m.name, 'Untitled Meeting')

    def test_dto_null_organizer(self):
        from ..services.normalizers.dto import BitrixMeeting
        m = BitrixMeeting(external_id=1, name='Test', organizer_bitrix_id='NULL')
        self.assertIsNone(m.organizer_bitrix_id)

    def test_dto_null_dates(self):
        from ..services.normalizers.dto import BitrixMeeting
        m = BitrixMeeting(
            external_id=1,
            name='Test',
            date_start='0000-00-00 00:00:00',
        )
        self.assertIsNone(m.date_start)


class TestMeetingCalendarEvent(TransactionCase):
    """Tests for calendar.event creation with Bitrix meeting data (2.11)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_calendar_event_has_bitrix_id(self):
        """calendar.event model has x_bitrix_id field."""
        fields = self.env['calendar.event'].fields_get()
        self.assertIn('x_bitrix_id', fields)

    def test_create_calendar_event(self):
        """Basic calendar.event creation with Bitrix fields."""
        event = self.env['calendar.event'].sudo().create({
            'name': 'Test Meeting',
            'start': '2024-01-15 10:00:00',
            'stop': '2024-01-15 11:00:00',
            'x_bitrix_id': '42',
        })
        self.assertTrue(event.exists())
        self.assertEqual(event.name, 'Test Meeting')
        self.assertEqual(str(event.x_bitrix_id), '42')

    def test_calendar_event_with_attendees(self):
        """Calendar event can have partner attendees."""
        partner1 = self.env['res.partner'].create({'name': 'Attendee 1'})
        partner2 = self.env['res.partner'].create({'name': 'Attendee 2'})
        event = self.env['calendar.event'].sudo().create({
            'name': 'Meeting with attendees',
            'start': '2024-01-15 10:00:00',
            'stop': '2024-01-15 11:00:00',
            'x_bitrix_id': '43',
            'partner_ids': [(6, 0, [partner1.id, partner2.id])],
        })
        self.assertEqual(len(event.partner_ids), 2)

    def test_calendar_event_idempotent_search(self):
        """Searching by x_bitrix_id finds existing event (idempotency)."""
        self.env['calendar.event'].sudo().create({
            'name': 'Existing Meeting',
            'start': '2024-01-15 10:00:00',
            'stop': '2024-01-15 11:00:00',
            'x_bitrix_id': '44',
        })
        found = self.env['calendar.event'].sudo().search(
            [('x_bitrix_id', '=', '44')],
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found.name, 'Existing Meeting')


class TestBitrixRRuleConverter(TransactionCase):
    """Pure-Python tests for the Bitrix RRULE -> Odoo recurrence converter."""

    def test_weekly_until(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        vals = _bitrix_rrule_to_odoo_recurrence(
            'FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO',
            dtstart=datetime(2025, 12, 22, 15, 30),
        )
        self.assertEqual(vals['rrule_type'], 'weekly')
        self.assertEqual(vals['interval'], 1)
        self.assertEqual(vals['end_type'], 'end_date')
        self.assertEqual(vals['until'], date(2038, 1, 1))
        self.assertTrue(vals['mon'])
        self.assertNotIn('tue', vals)

    def test_weekly_until_trailing_semicolon(self):
        """Bitrix sometimes appends trailing ; — must be ignored."""
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        vals = _bitrix_rrule_to_odoo_recurrence(
            'FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO;',
            dtstart=datetime(2025, 12, 22, 15, 30),
        )
        self.assertEqual(vals['rrule_type'], 'weekly')
        self.assertEqual(vals['until'], date(2038, 1, 1))

    def test_daily_count(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        vals = _bitrix_rrule_to_odoo_recurrence('FREQ=DAILY;COUNT=10;INTERVAL=2')
        self.assertEqual(vals['rrule_type'], 'daily')
        self.assertEqual(vals['interval'], 2)
        self.assertEqual(vals['end_type'], 'count')
        self.assertEqual(vals['count'], 10)

    def test_weekly_no_byday_falls_back_to_dtstart(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        # 2026-05-12 is a Tuesday
        vals = _bitrix_rrule_to_odoo_recurrence(
            'FREQ=WEEKLY', dtstart=datetime(2026, 5, 12, 10, 0),
        )
        self.assertEqual(vals['rrule_type'], 'weekly')
        self.assertEqual(vals['end_type'], 'forever')
        self.assertTrue(vals['tue'])

    def test_monthly_by_date(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        vals = _bitrix_rrule_to_odoo_recurrence('FREQ=MONTHLY;BYMONTHDAY=15')
        self.assertEqual(vals['month_by'], 'date')
        self.assertEqual(vals['day'], 15)
        self.assertEqual(vals['end_type'], 'forever')

    def test_monthly_by_nth_weekday(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        vals = _bitrix_rrule_to_odoo_recurrence('FREQ=MONTHLY;BYDAY=2FR')
        self.assertEqual(vals['month_by'], 'day')
        self.assertEqual(vals['byday'], '2')
        self.assertEqual(vals['weekday'], 'FR')

    def test_empty_or_invalid(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        self.assertIsNone(_bitrix_rrule_to_odoo_recurrence(''))
        self.assertIsNone(_bitrix_rrule_to_odoo_recurrence(None))
        self.assertIsNone(_bitrix_rrule_to_odoo_recurrence('NOT-A-RRULE'))

    def test_until_parser_variants(self):
        from ..services.loaders.meetings import _parse_bitrix_until
        self.assertEqual(_parse_bitrix_until('01.01.2038'), date(2038, 1, 1))
        self.assertEqual(_parse_bitrix_until('2038-01-01'), date(2038, 1, 1))
        self.assertEqual(_parse_bitrix_until('20380101T000000Z'), date(2038, 1, 1))
        self.assertIsNone(_parse_bitrix_until(''))
        self.assertIsNone(_parse_bitrix_until(None))

    def test_exdate_parser(self):
        from ..services.loaders.meetings import _parse_bitrix_exdate
        self.assertEqual(_parse_bitrix_exdate('29.12.2025'), [date(2025, 12, 29)])
        self.assertEqual(
            _parse_bitrix_exdate('29.12.2025,05.01.2026'),
            [date(2025, 12, 29), date(2026, 1, 5)],
        )
        self.assertEqual(
            _parse_bitrix_exdate('29.12.2025 15:30:00'),
            [date(2025, 12, 29)],
        )
        self.assertEqual(_parse_bitrix_exdate(''), [])
        self.assertEqual(_parse_bitrix_exdate(None), [])


class TestMeetingSQLFilter(TransactionCase):
    """Verify meeting SQL templates include new columns and bypass cut-off."""

    def test_meeting_sql_renders_with_guard(self):
        from ..services.extractors.bitrix_mysql import BitrixMySQLExtractor
        ex = BitrixMySQLExtractor(
            host='h', port=3306, user='u', password='p', database='d',
            date_from='2025-01-01',
        )
        sql = ex.SQL_MEETINGS_TEMPLATE.format(
            forum_topic_expr='NULL AS forum_topic_id',
            meeting_guard=ex._MEETING_GUARD,
            meeting_where_clause=ex._get_meeting_where_clause(),
        )
        # New columns must be present.
        self.assertIn('ce.RRULE AS rrule', sql)
        self.assertIn('ce.EXDATE AS exdate', sql)
        self.assertIn('ce.SECTION_ID AS section_id', sql)
        # Current guard filters by section external_type, not by child copies.
        self.assertIn('MEETING_HOST IS NOT NULL', sql)
        self.assertIn('b_calendar_section', sql)
        self.assertIn('EXTERNAL_TYPE', sql)
        # Recurring events bypass the date cut-off.
        self.assertIn("ce.RRULE IS NOT NULL AND ce.RRULE != ''", sql)


class TestMeetingRecurrenceWrite(TransactionCase):
    """Make sure Odoo accepts the recurrence vals we generate."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_weekly_recurring_event_persisted(self):
        from ..services.loaders.meetings import _bitrix_rrule_to_odoo_recurrence
        vals = _bitrix_rrule_to_odoo_recurrence(
            'FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO',
            dtstart=datetime(2025, 12, 22, 15, 30),
        )
        event_vals = {
            'name': 'Recurring weekly',
            'start': '2025-12-22 15:30:00',
            'stop': '2025-12-22 16:00:00',
            'x_bitrix_id': '64158',
            'recurrency': True,
        }
        event_vals.update(vals)
        event = self.env['calendar.event'].sudo().create(event_vals)
        self.assertTrue(event.recurrency)
        self.assertEqual(event.rrule_type, 'weekly')
        self.assertTrue(event.mon)
        self.assertFalse(event.tue)
        self.assertEqual(event.end_type, 'end_date')
        self.assertEqual(event.until, date(2038, 1, 1))

    def test_re_import_recurring_meeting_does_not_raise(self):
        """Re-importing the same recurring meeting must be a safe no-op.

        Regression: previously raised UserError('Unable to save the
        recurrence with "This Event"') when the record had recurrency=True
        but recurrence_id was cleared (Odoo's detach path).
        """
        from ..services.loaders.meetings import (
            _bitrix_rrule_to_odoo_recurrence, MeetingLoader,
        )

        rrule_vals = _bitrix_rrule_to_odoo_recurrence(
            'FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO',
            dtstart=datetime(2025, 12, 23, 15, 30),
        )
        base = self.env['calendar.event'].sudo().create({
            'name': 'Recurring re-import',
            'start': '2025-12-23 15:30:00',
            'stop': '2025-12-23 16:00:00',
            'x_bitrix_id': '99999',
            'recurrency': True,
            **rrule_vals,
        })
        self.env.cr.execute(
            "UPDATE calendar_event SET recurrence_id = NULL, active = FALSE "
            "WHERE id = %s",
            (base.id,),
        )
        base.invalidate_recordset()

        class _OneMeetingExtractor:
            def get_meetings(self_inner):
                return [{
                    'external_id': 99999,
                    'name': 'Recurring re-import',
                    'date_start': '2025-12-23 15:30:00',
                    'date_end': '2025-12-23 16:00:00',
                    'rrule': 'FREQ=WEEKLY;UNTIL=01.01.2038;INTERVAL=1;BYDAY=MO',
                    'exdate': None,
                    'section_id': None,
                    'organizer_bitrix_id': None,
                    'participant_bitrix_ids': None,
                    'description': '',
                    'forum_topic_id': None,
                }]

        loader = MeetingLoader(
            env=self.env, extractor=_OneMeetingExtractor(), dry_run=False,
        )
        loader.commit_checkpoint = lambda *a, **kw: None

        loader.run()

        self.assertEqual(loader.error_count, 0)
        self.assertEqual(loader.skipped_count, 1)
