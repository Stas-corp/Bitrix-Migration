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
        self.assertEqual(task.x_bitrix_responsible_employee_ids, self.emp_responsible)
        self.assertEqual(task.x_bitrix_accomplice_employee_ids, self.emp_accomplice)
        self.assertEqual(task.x_bitrix_auditor_employee_ids, self.emp_auditor)
        self.assertEqual(task.x_bitrix_originator_employee_id, self.emp_originator)

    # ── 1.02: Single responsible ─────────────────────────────────────

    def test_single_responsible(self):
        """Responsible field shows exactly one employee when one link exists."""
        task = self._create_task('Single Responsible')
        self._create_link(task, self.emp_responsible, 'responsible')
        task.invalidate_recordset()
        self.assertEqual(len(task.x_bitrix_responsible_employee_ids), 1)

    # ── 1.03: Accomplices separate from responsible ──────────────────

    def test_accomplice_separate(self):
        """Accomplices don't appear in responsible field."""
        task = self._create_task('Accomplice Test')
        self._create_link(task, self.emp_responsible, 'responsible')
        self._create_link(task, self.emp_accomplice, 'accomplice')
        task.invalidate_recordset()
        self.assertNotIn(
            self.emp_accomplice.id,
            task.x_bitrix_responsible_employee_ids.ids,
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
        self.assertNotIn(
            self.emp_auditor.id,
            task.x_bitrix_responsible_employee_ids.ids,
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
        self.assertFalse(task.x_bitrix_responsible_employee_ids)
        self.assertFalse(task.x_bitrix_accomplice_employee_ids)

    # ── 1.09: Rerun idempotency ─────────────────────────────────────

    def test_rerun_no_duplicates(self):
        """Writing same role twice via inverse doesn't create duplicates."""
        task = self._create_task('Rerun Test')
        # First write
        task.x_bitrix_responsible_employee_ids = self.emp_responsible
        task.x_bitrix_accomplice_employee_ids = self.emp_accomplice
        # Second write (simulates rerun)
        task.x_bitrix_responsible_employee_ids = self.emp_responsible
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
