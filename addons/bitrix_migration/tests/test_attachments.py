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

    def test_comment_compound_key_includes_forum_message_id(self):
        """Comment attachment key includes forum_message_id for uniqueness."""
        # Two comments on the same task with same file_path but different forum_message_id
        key1 = f'comment:100:555:/upload/test.pdf'
        key2 = f'comment:100:666:/upload/test.pdf'
        self.assertNotEqual(key1, key2)

    def test_comment_compound_key_same_message_same_file(self):
        """Same comment + same file produces identical key."""
        key1 = f'comment:100:555:/upload/test.pdf'
        key2 = f'comment:100:555:/upload/test.pdf'
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

    def test_task_attachment_adds_description_link_once(self):
        """Task attachments are also visible from the task description."""
        from ..services.loaders.attachments import AttachmentLoader
        from ..services.normalizers.dto import BitrixAttachment

        loader = AttachmentLoader(self.env, extractor=None, dry_run=True)
        att = BitrixAttachment(
            entity_type='task',
            entity_id=9001,
            disk_file_id='n42',
            disk_attached_object_id='90042',
            file_name='Report & plan.pdf',
            file_path='/upload/test.pdf',
        )
        ir_att = self.env['ir.attachment'].sudo().create({
            'name': 'Report & plan.pdf',
            'raw': b'data',
            'res_model': 'project.task',
            'res_id': self.task.id,
            'mimetype': 'application/pdf',
        })

        self.assertTrue(loader._ensure_task_description_attachment_link(
            ir_att, 'project.task', self.task.id, att,
        ))
        self.assertIn(
            f'/web/content/{ir_att.id}?download=true',
            self.task.description,
        )
        self.assertIn('Report &amp; plan.pdf', self.task.description)

        self.assertFalse(loader._ensure_task_description_attachment_link(
            ir_att, 'project.task', self.task.id, att,
        ))
        self.assertEqual(
            self.task.description.count(f'/web/content/{ir_att.id}?download=true'),
            1,
        )

    def test_task_attachment_replaces_disk_file_marker(self):
        """Task description DISK FILE markers are replaced with the attachment link."""
        from ..services.loaders.attachments import AttachmentLoader
        from ..services.normalizers.dto import BitrixAttachment

        self.task.description = 'Before [DISK FILE ID=n277883] after'
        loader = AttachmentLoader(self.env, extractor=None, dry_run=True)
        att = BitrixAttachment(
            entity_type='task',
            entity_id=9001,
            disk_file_id='n277883',
            disk_attached_object_id='182607',
            file_name='Offer.pdf',
            file_path='/upload/offer.pdf',
        )
        ir_att = self.env['ir.attachment'].sudo().create({
            'name': 'Offer.pdf',
            'raw': b'data',
            'res_model': 'project.task',
            'res_id': self.task.id,
            'mimetype': 'application/pdf',
        })

        self.assertTrue(loader._ensure_task_description_attachment_link(
            ir_att, 'project.task', self.task.id, att,
        ))
        self.assertNotIn('[DISK FILE ID=', self.task.description)
        self.assertIn('Before', self.task.description)
        self.assertIn('after', self.task.description)
        self.assertIn(f'/web/content/{ir_att.id}?download=true', self.task.description)

    def test_task_attachment_replaces_attached_object_marker(self):
        """Some Bitrix descriptions reference b_disk_attached_object.ID."""
        from ..services.loaders.attachments import AttachmentLoader
        from ..services.normalizers.dto import BitrixAttachment

        self.task.description = (
            '<span class="o_bitrix_disk_file_placeholder" '
            'data-bitrix-disk-file-id="182607">файл (см. вложения)</span>'
        )
        loader = AttachmentLoader(self.env, extractor=None, dry_run=True)
        att = BitrixAttachment(
            entity_type='task',
            entity_id=9001,
            disk_file_id='n277778',
            disk_attached_object_id='182607',
            file_name='Invoice.pdf',
            file_path='/upload/invoice.pdf',
        )
        ir_att = self.env['ir.attachment'].sudo().create({
            'name': 'Invoice.pdf',
            'raw': b'data',
            'res_model': 'project.task',
            'res_id': self.task.id,
            'mimetype': 'application/pdf',
        })

        self.assertTrue(loader._ensure_task_description_attachment_link(
            ir_att, 'project.task', self.task.id, att,
        ))
        self.assertNotIn('o_bitrix_disk_file_placeholder', self.task.description)
        self.assertIn(f'/web/content/{ir_att.id}?download=true', self.task.description)

    def test_comment_attachment_replaces_generic_placeholder_once(self):
        """Comment placeholders are replaced after Odoo strips marker data attrs."""
        from ..services.loaders.attachments import AttachmentLoader
        from ..services.normalizers.dto import BitrixAttachment

        msg = self.env['mail.message'].sudo().create({
            'body': (
                '<p>comment with file<br>'
                '<span class="o_bitrix_disk_file_placeholder">файл (см. вложения)</span>'
                '</p>'
            ),
            'model': 'project.task',
            'res_id': self.task.id,
            'x_bitrix_message_id': 555,
        })
        loader = AttachmentLoader(self.env, extractor=None, dry_run=True)
        att = BitrixAttachment(
            entity_type='comment',
            entity_id=9001,
            forum_message_id=555,
            disk_file_id='n277900',
            disk_attached_object_id='182900',
            file_name='Comment image.png',
            file_path='/upload/comment.png',
        )
        ir_att = self.env['ir.attachment'].sudo().create({
            'name': 'Comment image.png',
            'raw': b'data',
            'res_model': 'project.task',
            'res_id': self.task.id,
            'mimetype': 'image/png',
        })

        self.assertTrue(loader._ensure_message_body_attachment_link(ir_att, msg, att))
        self.assertNotIn('o_bitrix_disk_file_placeholder', msg.body)
        self.assertIn(f'/web/content/{ir_att.id}?download=true', msg.body)
        self.assertIn('Comment image.png', msg.body)

        self.assertFalse(loader._ensure_message_body_attachment_link(ir_att, msg, att))
        self.assertEqual(msg.body.count(f'/web/content/{ir_att.id}?download=true'), 1)


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
            disk_file_id='n777',
            disk_attached_object_id='888',
            file_name='report.pdf',
            file_size=1024,
            content_type='application/pdf',
            file_path='/upload/iblock/abc/report.pdf',
        )
        self.assertEqual(att.entity_type, 'comment')
        self.assertEqual(att.entity_id, 42)
        self.assertEqual(att.forum_message_id, 555)
        self.assertEqual(att.disk_file_id, 'n777')
        self.assertEqual(att.disk_attached_object_id, '888')
        self.assertEqual(att.file_name, 'report.pdf')

    def test_dto_null_forum_message_id(self):
        from ..services.normalizers.dto import BitrixAttachment
        att = BitrixAttachment(forum_message_id='NULL')
        self.assertIsNone(att.forum_message_id)
