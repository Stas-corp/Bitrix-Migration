from odoo.tests.common import TransactionCase


class TestMeetingAttachmentResolution(TransactionCase):
    """Tests for meeting and meeting-comment attachment parent resolution."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })
        cls.project = cls.env['project.project'].create({'name': 'Meeting Att Project'})
        cls.task = cls.env['project.task'].create({
            'name': 'Meeting Att Task',
            'project_id': cls.project.id,
            'x_bitrix_id': '100',
        })
        cls.event = cls.env['calendar.event'].sudo().create({
            'name': 'Meeting Att Event',
            'start': '2024-01-15 10:00:00',
            'stop': '2024-01-15 11:00:00',
            'x_bitrix_id': '42',
        })
        cls.message = cls.env['mail.message'].sudo().create({
            'body': 'meeting message',
            'model': 'calendar.event',
            'res_id': cls.event.id,
            'x_bitrix_message_id': '777',
        })

    def _loader(self):
        from ..services.loaders.attachments import AttachmentLoader

        return AttachmentLoader(self.env, extractor=None, dry_run=True)

    def _attachment(self, entity_type, entity_id, forum_message_id=None):
        from ..services.normalizers.dto import BitrixAttachment

        return BitrixAttachment(
            entity_type=entity_type,
            entity_id=entity_id,
            forum_message_id=forum_message_id,
            file_path='/upload/test.pdf',
        )

    def test_resolve_parent_all_attachment_types(self):
        loader = self._loader()
        task_map = {'100': self.task.id}
        meeting_map = {'42': self.event.id}
        message_map = {'777': self.message.id}

        self.assertEqual(
            loader._resolve_parent(
                'task', self._attachment('task', 100), task_map, {}, {},
            ),
            ('project.task', self.task.id),
        )
        self.assertEqual(
            loader._resolve_parent(
                'comment',
                self._attachment('comment', 100, forum_message_id=777),
                task_map,
                message_map,
                {},
            ),
            ('mail.message', self.message.id),
        )
        self.assertEqual(
            loader._resolve_parent(
                'meeting', self._attachment('meeting', 42), {}, {}, meeting_map,
            ),
            ('calendar.event', self.event.id),
        )
        self.assertEqual(
            loader._resolve_parent(
                'meeting_comment',
                self._attachment('meeting_comment', 42, forum_message_id=777),
                {},
                message_map,
                meeting_map,
            ),
            ('mail.message', self.message.id),
        )

    def test_meeting_comment_fallback_to_event(self):
        loader = self._loader()
        att = self._attachment('meeting_comment', 42)

        self.assertEqual(
            loader._resolve_parent(
                'meeting_comment', att, {}, {}, {'42': self.event.id},
            ),
            ('calendar.event', self.event.id),
        )

    def test_meeting_compound_keys(self):
        loader = self._loader()

        meeting_att = self._attachment('meeting', 42)
        meeting_comment_att = self._attachment(
            'meeting_comment', 42, forum_message_id=777,
        )

        self.assertEqual(
            loader._make_compound_key('meeting', meeting_att),
            'meeting:42:/upload/test.pdf',
        )
        self.assertEqual(
            loader._make_compound_key('meeting_comment', meeting_comment_att),
            'meeting_comment:42:777:/upload/test.pdf',
        )
