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
        )
        self.assertEqual(m.external_id, 42)
        self.assertEqual(m.name, 'Sprint Review')
        self.assertIsNotNone(m.date_start)
        self.assertIsNotNone(m.date_end)
        self.assertEqual(m.participant_bitrix_ids, '1, 2, 3')
        self.assertEqual(m.organizer_bitrix_id, 1)
        self.assertEqual(m.forum_topic_id, 123)

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
