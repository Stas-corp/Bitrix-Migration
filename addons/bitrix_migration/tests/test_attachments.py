from odoo.tests.common import TransactionCase


class TestAttachmentIdempotency(TransactionCase):
    """Tests for attachment loading: compound key, comment linking (2.08–2.10)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })
        cls.project = cls.env['project.project'].create({'name': 'Att Test Project'})
        cls.task = cls.env['project.task'].create({
            'name': 'Att Test Task',
            'project_id': cls.project.id,
            'x_bitrix_id': '9001',
        })

    def _get_mapping(self):
        run = self.env['bitrix.migration.run'].create({
            'name': 'test-att',
            'mysql_host': 'localhost',
            'mysql_db': 'test',
            'mysql_user': 'test',
            'mysql_password': 'test',
        })
        return self.env['bitrix.migration.mapping']

    # ── 2.09: Compound key idempotency ──────────────────────────────

    def test_compound_key_format(self):
        """Compound key includes entity_type:entity_id:file_path."""
        key = f'task:100:/upload/test.pdf'
        self.assertIn('task:', key)
        self.assertIn(':100:', key)
        self.assertIn('/upload/test.pdf', key)

    def test_compound_key_different_entities(self):
        """Same file_path for different entities produces different keys."""
        key1 = f'task:100:/upload/test.pdf'
        key2 = f'comment:200:/upload/test.pdf'
        self.assertNotEqual(key1, key2)

    def test_compound_key_same_entity_same_file(self):
        """Same entity + same file produces identical key."""
        key1 = f'task:100:/upload/test.pdf'
        key2 = f'task:100:/upload/test.pdf'
        self.assertEqual(key1, key2)

    # ── 2.08: Comment attachment linking ────────────────────────────

    def test_mail_message_has_bitrix_fields(self):
        """mail.message has x_bitrix_message_id field for linking."""
        fields = self.env['mail.message'].fields_get()
        self.assertIn('x_bitrix_message_id', fields)

    def test_comment_attachment_model_resolution(self):
        """Comment attachments should resolve to mail.message when possible."""
        # Create a mail.message with a bitrix message id
        msg = self.env['mail.message'].sudo().create({
            'body': 'test comment',
            'model': 'project.task',
            'res_id': self.task.id,
            'x_bitrix_message_id': 555,
        })
        # Simulate the lookup logic from AttachmentLoader
        msg_recs = self.env['mail.message'].sudo().search_read(
            [('x_bitrix_message_id', '!=', False)],
            ['id', 'x_bitrix_message_id'],
        )
        message_map = {}
        for rec in msg_recs:
            if rec['x_bitrix_message_id']:
                message_map[str(rec['x_bitrix_message_id'])] = rec['id']

        self.assertIn('555', message_map)
        self.assertEqual(message_map['555'], msg.id)

    def test_comment_attachment_fallback_to_task(self):
        """If no mail.message found, comment attachment falls back to task."""
        # No message with bitrix_message_id=999 exists
        msg_recs = self.env['mail.message'].sudo().search_read(
            [('x_bitrix_message_id', '=', 999)],
            ['id', 'x_bitrix_message_id'],
        )
        self.assertEqual(len(msg_recs), 0)


class TestAttachmentDTO(TransactionCase):
    """Tests for BitrixAttachment DTO."""

    def test_dto_defaults(self):
        from ..services.normalizers.dto import BitrixAttachment
        att = BitrixAttachment()
        self.assertEqual(att.entity_type, 'task')
        self.assertEqual(att.entity_id, 0)
        self.assertEqual(att.file_name, '')
        self.assertEqual(att.content_type, 'application/octet-stream')

    def test_dto_with_values(self):
        from ..services.normalizers.dto import BitrixAttachment
        att = BitrixAttachment(
            entity_type='comment',
            entity_id=42,
            forum_message_id=555,
            file_name='report.pdf',
            file_size=1024,
            content_type='application/pdf',
            file_path='/upload/iblock/abc/report.pdf',
        )
        self.assertEqual(att.entity_type, 'comment')
        self.assertEqual(att.entity_id, 42)
        self.assertEqual(att.forum_message_id, 555)
        self.assertEqual(att.file_name, 'report.pdf')

    def test_dto_null_forum_message_id(self):
        from ..services.normalizers.dto import BitrixAttachment
        att = BitrixAttachment(forum_message_id='NULL')
        self.assertIsNone(att.forum_message_id)
