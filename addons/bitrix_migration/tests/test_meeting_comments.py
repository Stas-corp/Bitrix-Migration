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

