from odoo.tests.common import TransactionCase


class _MeetingCommentExtractor:
    def get_meeting_comments(self):
        return [{
            'document_model': 'calendar.event',
            'entity_id': 10,
            'message_id': 777,
            'type': 'comment',
            'body': 'Meeting comment body',
            'date': '2024-01-15 10:30:00',
            'author_bitrix_id': 0,
        }]


class TestMeetingComments(TransactionCase):
    """Tests for loading Bitrix meeting comments onto calendar.event."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_comment_dto_accepts_calendar_event_model(self):
        from ..services.normalizers.dto import BitrixComment

        comment = BitrixComment(
            document_model='calendar.event',
            entity_id=10,
            message_id=777,
            body='Hello',
        )

        self.assertEqual(comment.document_model, 'calendar.event')
        self.assertEqual(comment.entity_id, 10)

    def test_loader_creates_mail_message_on_calendar_event(self):
        from ..services.loaders.comments import CommentLoader

        event = self.env['calendar.event'].sudo().create({
            'name': 'Meeting with comments',
            'start': '2024-01-15 10:00:00',
            'stop': '2024-01-15 11:00:00',
            'x_bitrix_id': '10',
        })
        self.env['bitrix.migration.mapping'].sudo().set_mapping(
            '10', 'meeting', 'calendar.event', event.id,
        )

        loader = CommentLoader(
            env=self.env,
            extractor=_MeetingCommentExtractor(),
            document_model='calendar.event',
            source_entity_type='meeting',
            entity_type='meeting_comment',
        )
        loader.run()

        message = self.env['mail.message'].sudo().search([
            ('x_bitrix_message_id', '=', '777'),
        ], limit=1)
        self.assertTrue(message)
        self.assertEqual(message.model, 'calendar.event')
        self.assertEqual(message.res_id, event.id)
        self.assertIn('Meeting comment body', message.body)
