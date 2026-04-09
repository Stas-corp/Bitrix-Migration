from odoo.tests.common import TransactionCase


class TestRoleMapping(TransactionCase):
    """Tests for Bitrix task participant role mapping (1.12)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })
        # Create a project
        cls.project = cls.env['project.project'].create({'name': 'Test Project'})
        # Create employees for each role
        cls.emp_responsible = cls.env['hr.employee'].create({
            'name': 'Responsible Employee',
            'x_bitrix_id': 101,
        })
        cls.emp_accomplice = cls.env['hr.employee'].create({
            'name': 'Accomplice Employee',
            'x_bitrix_id': 102,
        })
        cls.emp_auditor = cls.env['hr.employee'].create({
            'name': 'Auditor Employee',
            'x_bitrix_id': 103,
        })
        cls.emp_originator = cls.env['hr.employee'].create({
            'name': 'Originator Employee',
            'x_bitrix_id': 104,
        })
        cls.emp_creator = cls.env['hr.employee'].create({
            'name': 'Creator Employee',
            'x_bitrix_id': 105,
        })

    def _create_task(self, name='Test Task'):
        return self.env['project.task'].create({
            'name': name,
            'project_id': self.project.id,
            'x_bitrix_id': str(hash(name) % 100000),
        })

    def _create_link(self, task, employee, role):
        return self.env['bitrix.task.employee.link'].create({
            'task_id': task.id,
            'employee_id': employee.id,
            'role': role,
        })

    # ── 1.01: Roles are stored separately ────────────────────────────

    def test_roles_stored_separately(self):
        """Each Bitrix role maps to a distinct link record."""
        task = self._create_task('Separate Roles')
        self._create_link(task, self.emp_responsible, 'responsible')
        self._create_link(task, self.emp_accomplice, 'accomplice')
        self._create_link(task, self.emp_auditor, 'auditor')
        self._create_link(task, self.emp_originator, 'originator')
        self._create_link(task, self.emp_creator, 'creator')

        task.invalidate_recordset()
        self.assertEqual(task.x_bitrix_responsible_employee_id, self.emp_responsible)
        self.assertEqual(task.x_bitrix_accomplice_employee_ids, self.emp_accomplice)
        self.assertEqual(task.x_bitrix_auditor_employee_ids, self.emp_auditor)
        self.assertEqual(task.x_bitrix_originator_employee_id, self.emp_originator)

    # ── 1.02: Single responsible (canonical Many2one) ────────────────

    def test_single_responsible_canonical(self):
        """Canonical responsible field is Many2one, shows exactly one employee."""
        task = self._create_task('Single Responsible')
        self._create_link(task, self.emp_responsible, 'responsible')
        task.invalidate_recordset()
        self.assertEqual(task.x_bitrix_responsible_employee_id, self.emp_responsible)
        # Deprecated mirror also shows the same
        self.assertEqual(len(task.x_bitrix_responsible_employee_ids), 1)

    # ── Multiple source R collapse to one canonical responsible ──────

    def test_multiple_source_responsible_collapse(self):
        """Multiple source R entries collapse: only first is stored as responsible link.

        Since we have a partial unique index, only one responsible link per task can exist.
        """
        task = self._create_task('Multi R Test')
        self._create_link(task, self.emp_responsible, 'responsible')
        # Attempting to create a second responsible link should fail
        with self.assertRaises(Exception):
            self._create_link(task, self.emp_accomplice, 'responsible')

    # ── 1.03: Accomplices separate from responsible ──────────────────

    def test_accomplice_separate(self):
        """Accomplices don't appear in responsible field."""
        task = self._create_task('Accomplice Test')
        self._create_link(task, self.emp_responsible, 'responsible')
        self._create_link(task, self.emp_accomplice, 'accomplice')
        task.invalidate_recordset()
        self.assertNotEqual(
            task.x_bitrix_responsible_employee_id,
            self.emp_accomplice,
        )
        self.assertIn(
            self.emp_accomplice.id,
            task.x_bitrix_accomplice_employee_ids.ids,
        )

    # ── 1.04: Auditors separate ─────────────────────────────────────

    def test_auditor_separate(self):
        """Auditors don't appear in responsible or accomplice fields."""
        task = self._create_task('Auditor Test')
        self._create_link(task, self.emp_responsible, 'responsible')
        self._create_link(task, self.emp_auditor, 'auditor')
        task.invalidate_recordset()
        self.assertNotEqual(
            task.x_bitrix_responsible_employee_id,
            self.emp_auditor,
        )
        self.assertNotIn(
            self.emp_auditor.id,
            task.x_bitrix_accomplice_employee_ids.ids,
        )
        self.assertIn(
            self.emp_auditor.id,
            task.x_bitrix_auditor_employee_ids.ids,
        )

    # ── 1.05: Originator and creator separate ────────────────────────

    def test_originator_and_creator_separate(self):
        """Originator and creator don't appear in assignee fields."""
        task = self._create_task('Originator Creator Test')
        self._create_link(task, self.emp_originator, 'originator')
        task.write({'x_bitrix_creator_employee_id': self.emp_creator.id})
        task.invalidate_recordset()
        self.assertEqual(task.x_bitrix_originator_employee_id, self.emp_originator)
        self.assertEqual(task.x_bitrix_creator_employee_id, self.emp_creator)
        self.assertFalse(task.x_bitrix_responsible_employee_id)
        self.assertFalse(task.x_bitrix_accomplice_employee_ids)

    # ── 1.09: Rerun idempotency ─────────────────────────────────────

    def test_rerun_no_duplicates(self):
        """Writing same role twice via inverse doesn't create duplicates."""
        task = self._create_task('Rerun Test')
        # First write
        task.x_bitrix_responsible_employee_id = self.emp_responsible
        task.x_bitrix_accomplice_employee_ids = self.emp_accomplice
        # Second write (simulates rerun)
        task.x_bitrix_responsible_employee_id = self.emp_responsible
        task.x_bitrix_accomplice_employee_ids = self.emp_accomplice

        Link = self.env['bitrix.task.employee.link']
        responsible_links = Link.search([
            ('task_id', '=', task.id),
            ('role', '=', 'responsible'),
        ])
        accomplice_links = Link.search([
            ('task_id', '=', task.id),
            ('role', '=', 'accomplice'),
        ])
        self.assertEqual(len(responsible_links), 1)
        self.assertEqual(len(accomplice_links), 1)

    # ── Unique constraint ────────────────────────────────────────────

    def test_unique_constraint(self):
        """Same employee+role+task can't be duplicated."""
        task = self._create_task('Unique Test')
        self._create_link(task, self.emp_responsible, 'responsible')
        # Second insert should conflict on unique index
        with self.assertRaises(Exception):
            self._create_link(task, self.emp_responsible, 'responsible')

    # ── Creator/auditor do not pollute user_ids ──────────────────────

    def test_creator_only_not_in_user_ids(self):
        """A creator-only employee (not responsible/accomplice) must not appear in user_ids."""
        user = self.env['res.users'].create({
            'name': 'Creator User',
            'login': 'creator_test_user@example.com',
            'email': 'creator_test_user@example.com',
        })
        self.emp_creator.write({'user_id': user.id})

        task = self._create_task('Creator Only')
        task.write({'x_bitrix_creator_employee_id': self.emp_creator.id})
        task.invalidate_recordset()

        # user_ids should NOT include the creator-only user
        self.assertNotIn(user.id, task.user_ids.ids)

    def test_auditor_only_not_in_user_ids(self):
        """An auditor-only employee (not responsible/accomplice) must not appear in user_ids."""
        user = self.env['res.users'].create({
            'name': 'Auditor User',
            'login': 'auditor_test_user@example.com',
            'email': 'auditor_test_user@example.com',
        })
        self.emp_auditor.write({'user_id': user.id})

        task = self._create_task('Auditor Only')
        self._create_link(task, self.emp_auditor, 'auditor')
        task.invalidate_recordset()

        # user_ids should NOT include the auditor-only user
        self.assertNotIn(user.id, task.user_ids.ids)

    def test_responsible_accomplice_in_user_ids_after_recompute(self):
        """Responsible + accomplice users should end up in user_ids after recompute."""
        user_r = self.env['res.users'].create({
            'name': 'Resp User',
            'login': 'resp_test_user@example.com',
            'email': 'resp_test_user@example.com',
        })
        user_a = self.env['res.users'].create({
            'name': 'Acc User',
            'login': 'acc_test_user@example.com',
            'email': 'acc_test_user@example.com',
        })
        self.emp_responsible.write({'user_id': user_r.id})
        self.emp_accomplice.write({'user_id': user_a.id})

        task = self._create_task('RA user_ids Test')
        self._create_link(task, self.emp_responsible, 'responsible')
        self._create_link(task, self.emp_accomplice, 'accomplice')
        task.invalidate_recordset()

        # Use the shared recompute helper
        from ..services.loaders.base import BaseLoader
        loader = BaseLoader(env=self.env, extractor=None)
        loader.recompute_task_user_ids(task)
        task.invalidate_recordset()

        self.assertIn(user_r.id, task.user_ids.ids)
        self.assertIn(user_a.id, task.user_ids.ids)

    # ── DTO parsing ──────────────────────────────────────────────────

    def test_dto_new_fields(self):
        """BitrixTask DTO accepts accomplice_user_ids and originator_user_ids."""
        from ..services.normalizers.dto import BitrixTask
        task = BitrixTask(
            external_id=999,
            name='DTO Test',
            responsible_user_ids='1, 2',
            accomplice_user_ids='3, 4',
            auditor_user_ids='5',
            originator_user_ids='6',
            creator_bitrix_id=7,
        )
        self.assertEqual(task.responsible_user_ids, '1, 2')
        self.assertEqual(task.accomplice_user_ids, '3, 4')
        self.assertEqual(task.auditor_user_ids, '5')
        self.assertEqual(task.originator_user_ids, '6')
        self.assertEqual(task.creator_bitrix_id, 7)

    def test_dto_empty_new_fields(self):
        """New DTO fields default to None when absent."""
        from ..services.normalizers.dto import BitrixTask
        task = BitrixTask(external_id=998, name='Minimal')
        self.assertIsNone(task.accomplice_user_ids)
        self.assertIsNone(task.originator_user_ids)


class TestAssigneeUserIds(TransactionCase):
    """Tests for x_bitrix_assignee_user_ids canonical assignee field (Round 2)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })
        cls.project = cls.env['project.project'].create({'name': 'Assignee Test Project'})

        # Employee with a linked user (responsible)
        cls.user_r = cls.env['res.users'].create({
            'name': 'Assignee Resp User',
            'login': 'assignee_resp@example.com',
            'email': 'assignee_resp@example.com',
        })
        cls.emp_r = cls.env['hr.employee'].create({
            'name': 'Assignee Resp Employee',
            'x_bitrix_id': 201,
            'user_id': cls.user_r.id,
        })
        # Employee with a linked user (accomplice)
        cls.user_a = cls.env['res.users'].create({
            'name': 'Assignee Acc User',
            'login': 'assignee_acc@example.com',
            'email': 'assignee_acc@example.com',
        })
        cls.emp_a = cls.env['hr.employee'].create({
            'name': 'Assignee Acc Employee',
            'x_bitrix_id': 202,
            'user_id': cls.user_a.id,
        })
        # Employee without linked user (auditor — should NOT be in assignees)
        cls.emp_auditor = cls.env['hr.employee'].create({
            'name': 'Assignee Auditor Employee',
            'x_bitrix_id': 203,
        })
        cls.user_u = cls.env['res.users'].create({
            'name': 'Assignee Watcher User',
            'login': 'assignee_watcher@example.com',
            'email': 'assignee_watcher@example.com',
        })
        cls.emp_auditor_user = cls.env['hr.employee'].create({
            'name': 'Assignee Watcher Employee',
            'x_bitrix_id': 204,
            'user_id': cls.user_u.id,
        })

    def _create_task(self, name='Assignee Task'):
        return self.env['project.task'].create({
            'name': name,
            'project_id': self.project.id,
            'x_bitrix_id': str(abs(hash(name)) % 100000 + 200000),
        })

    # ── Field existence ──────────────────────────────────────────────

    def test_field_exists(self):
        """project.task must have x_bitrix_assignee_user_ids field."""
        fields = self.env['project.task'].fields_get()
        self.assertIn('x_bitrix_assignee_user_ids', fields)

    def test_field_is_many2many_to_res_users(self):
        """x_bitrix_assignee_user_ids is a Many2many to res.users."""
        field_info = self.env['project.task'].fields_get()['x_bitrix_assignee_user_ids']
        self.assertEqual(field_info['type'], 'many2many')
        self.assertEqual(field_info['relation'], 'res.users')

    # ── Stored assignee values ───────────────────────────────────────

    def test_assignee_user_ids_stored(self):
        """x_bitrix_assignee_user_ids can be written and read back."""
        task = self._create_task('Stored Assignees')
        task.write({
            'x_bitrix_assignee_user_ids': [(6, 0, [self.user_r.id, self.user_a.id])],
        })
        task.invalidate_recordset()
        self.assertEqual(sorted(task.x_bitrix_assignee_user_ids.ids),
                         sorted([self.user_r.id, self.user_a.id]))

    # ── recompute_task_user_ids mirrors x_bitrix_assignee_user_ids ──

    def test_recompute_mirrors_assignee_user_ids(self):
        """recompute_task_user_ids copies x_bitrix_assignee_user_ids → user_ids."""
        task = self._create_task('Recompute Mirror')
        task.write({
            'x_bitrix_assignee_user_ids': [(6, 0, [self.user_r.id, self.user_a.id])],
        })

        from ..services.loaders.base import BaseLoader
        loader = BaseLoader(env=self.env, extractor=None)
        loader.recompute_task_user_ids(task)
        task.invalidate_recordset()

        self.assertEqual(sorted(task.user_ids.ids),
                         sorted([self.user_r.id, self.user_a.id]))

    def test_recompute_fallback_from_employee_links(self):
        """When x_bitrix_assignee_user_ids is empty, recompute resolves from employee links."""
        task = self._create_task('Recompute Fallback')
        # Set up links via computed inverse
        task.x_bitrix_responsible_employee_id = self.emp_r
        task.x_bitrix_accomplice_employee_ids = self.emp_a
        task.invalidate_recordset()

        # Ensure assignee_user_ids is empty so fallback triggers
        task.write({'x_bitrix_assignee_user_ids': [(5, 0, 0)]})
        task.invalidate_recordset()
        self.assertFalse(task.x_bitrix_assignee_user_ids.ids)

        from ..services.loaders.base import BaseLoader
        loader = BaseLoader(env=self.env, extractor=None)
        loader.recompute_task_user_ids(task)
        task.invalidate_recordset()

        # Both should be resolved
        self.assertIn(self.user_r.id, task.user_ids.ids)
        self.assertIn(self.user_a.id, task.user_ids.ids)
        # And canonical field should now be populated
        self.assertTrue(task.x_bitrix_assignee_user_ids.ids)

    def test_recompute_idempotent(self):
        """Running recompute twice produces the same result."""
        task = self._create_task('Recompute Idempotent')
        task.write({
            'x_bitrix_assignee_user_ids': [(6, 0, [self.user_r.id])],
        })

        from ..services.loaders.base import BaseLoader
        loader = BaseLoader(env=self.env, extractor=None)
        loader.recompute_task_user_ids(task)
        task.invalidate_recordset()
        first_ids = sorted(task.user_ids.ids)

        loader.recompute_task_user_ids(task)
        task.invalidate_recordset()
        second_ids = sorted(task.user_ids.ids)

        self.assertEqual(first_ids, second_ids)

    def test_auditor_not_in_assignee_user_ids(self):
        """Auditor-only employee should not end up in x_bitrix_assignee_user_ids."""
        task = self._create_task('Auditor Excluded')
        self.env['bitrix.task.employee.link'].create({
            'task_id': task.id,
            'employee_id': self.emp_auditor.id,
            'role': 'auditor',
        })
        task.invalidate_recordset()

        # x_bitrix_assignee_user_ids should remain empty
        self.assertFalse(task.x_bitrix_assignee_user_ids.ids)

        from ..services.loaders.base import BaseLoader
        loader = BaseLoader(env=self.env, extractor=None)
        loader.recompute_task_user_ids(task)
        task.invalidate_recordset()

        # user_ids should also be empty
        self.assertFalse(task.user_ids.ids)

    def test_responsible_user_field_syncs_employee_and_access(self):
        """Editing responsible by user updates employee link and assignee/access mirrors."""
        task = self._create_task('Responsible User Field')
        task.write({'x_bitrix_responsible_user_id': self.user_r.id})
        task.invalidate_recordset()

        self.assertEqual(task.x_bitrix_responsible_employee_id, self.emp_r)
        self.assertEqual(task.x_bitrix_responsible_user_id, self.user_r)
        self.assertIn(self.user_r.id, task.x_bitrix_assignee_user_ids.ids)
        self.assertIn(self.user_r.id, task.user_ids.ids)
        self.assertIn(self.user_r.id, task.x_bitrix_access_user_ids.ids)

    def test_auditor_user_field_syncs_employee_and_access_only(self):
        """Editing auditors by user updates employee links and grants access without assignee role."""
        task = self._create_task('Auditor User Field')
        task.write({'x_bitrix_auditor_user_ids': [(6, 0, [self.user_u.id])]})
        task.invalidate_recordset()

        self.assertIn(self.emp_auditor_user.id, task.x_bitrix_auditor_employee_ids.ids)
        self.assertIn(self.user_u.id, task.x_bitrix_auditor_user_ids.ids)
        self.assertFalse(task.x_bitrix_assignee_user_ids.ids)
        self.assertFalse(task.user_ids.ids)
        self.assertIn(self.user_u.id, task.x_bitrix_access_user_ids.ids)

    def test_task_search_supports_auditor_user_domain(self):
        """Searching through auditor.user_id works for computed Bitrix auditor field."""
        task = self._create_task('Auditor User Search Task')
        task.write({'x_bitrix_auditor_user_ids': [(6, 0, [self.user_u.id])]})
        task.invalidate_recordset()

        found = self.env['project.task'].search([
            ('id', '=', task.id),
            ('x_bitrix_auditor_employee_ids.user_id', '=', self.user_u.id),
        ])

        self.assertEqual(found, task)

    def test_project_search_supports_task_auditor_user_domain(self):
        """Project search works through task_ids.x_bitrix_auditor_employee_ids.user_id."""
        task = self._create_task('Auditor User Search Project')
        task.write({'x_bitrix_auditor_user_ids': [(6, 0, [self.user_u.id])]})
        task.invalidate_recordset()

        found = self.env['project.project'].search([
            ('id', '=', self.project.id),
            ('task_ids.x_bitrix_auditor_employee_ids.user_id', '=', self.user_u.id),
        ])

        self.assertEqual(found, self.project)

    def test_user_ids_write_updates_assignee_and_access_mirrors(self):
        """Manual assignee edits keep Bitrix assignee/access mirrors aligned."""
        task = self._create_task('Manual user_ids Sync')
        task.write({'user_ids': [(6, 0, [self.user_r.id])]})
        task.invalidate_recordset()

        self.assertEqual(task.x_bitrix_assignee_user_ids.ids, [self.user_r.id])
        self.assertEqual(task.x_bitrix_access_user_ids.ids, [self.user_r.id])


class TestFixAttachmentRelink(TransactionCase):
    """Tests for fix_attachments mode: relinking comment attachments to mail.message."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })
        cls.project = cls.env['project.project'].create({'name': 'Fix Att Project'})
        cls.task = cls.env['project.task'].create({
            'name': 'Fix Att Task',
            'project_id': cls.project.id,
            'x_bitrix_id': '8001',
        })

    def test_comment_attachment_on_task_detectable(self):
        """Comment attachment linked to project.task (not mail.message) is detectable."""
        # Create a mail.message with bitrix_message_id
        msg = self.env['mail.message'].sudo().create({
            'body': 'test comment for relink',
            'model': 'project.task',
            'res_id': self.task.id,
            'x_bitrix_message_id': 777,
        })

        # Create an ir.attachment linked to project.task (wrong)
        att = self.env['ir.attachment'].sudo().create({
            'name': 'mislinked.pdf',
            'datas': False,
            'res_model': 'project.task',
            'res_id': self.task.id,
        })

        # The attachment is linked to task instead of message
        self.assertEqual(att.res_model, 'project.task')
        self.assertEqual(att.res_id, self.task.id)

        # A correct relink would change it to mail.message
        att.write({'res_model': 'mail.message', 'res_id': msg.id})
        self.assertEqual(att.res_model, 'mail.message')
        self.assertEqual(att.res_id, msg.id)

    def test_relink_preserves_attachment_record(self):
        """Relinking changes res_model/res_id in-place without creating duplicates."""
        msg = self.env['mail.message'].sudo().create({
            'body': 'comment for preserve test',
            'model': 'project.task',
            'res_id': self.task.id,
            'x_bitrix_message_id': 778,
        })

        att = self.env['ir.attachment'].sudo().create({
            'name': 'preserve.pdf',
            'datas': False,
            'res_model': 'project.task',
            'res_id': self.task.id,
        })
        att_id = att.id

        # Relink
        att.write({'res_model': 'mail.message', 'res_id': msg.id})

        # Same record, just updated fields
        att.invalidate_recordset()
        self.assertEqual(att.id, att_id)
        self.assertEqual(att.res_model, 'mail.message')

    def test_canonical_compound_key_parsing(self):
        """Canonical key comment:task_id:forum_msg_id:file_path parses correctly."""
        key = 'comment:8001:777:/upload/iblock/abc/doc.pdf'
        parts = key.split(':')
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], 'comment')
        self.assertEqual(parts[1], '8001')
        self.assertEqual(parts[2], '777')
        self.assertEqual(parts[3], '/upload/iblock/abc/doc.pdf')

    def test_legacy_key_without_forum_message_id(self):
        """Legacy key comment:task_id:file_path has only 3 parts — no forum_message_id."""
        key = 'comment:8001:/upload/iblock/abc/doc.pdf'
        parts = key.split(':')
        self.assertEqual(len(parts), 3)
        # Cannot extract forum_message_id from legacy key
        self.assertEqual(parts[0], 'comment')
        self.assertEqual(parts[1], '8001')
