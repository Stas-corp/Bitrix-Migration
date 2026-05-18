"""Tests for fired (ACTIVE='N') employee import and re-activation guard."""
from odoo.tests.common import TransactionCase

from ..services.loaders.employees import EmployeeLoader
from ..services.normalizers.dto import BitrixEmployee


class _FakeExtractor:
    """Minimal extractor stub: serves a static set of employee rows."""

    def __init__(self, rows):
        self.rows = rows

    def get_employees(self):
        return self.rows

    def get_employee_telegrams(self):
        return {}


class _TestEmployeeLoader(EmployeeLoader):
    """EmployeeLoader override: no commit/checkpoint inside a test transaction."""

    def commit_checkpoint(self, count, last_bitrix_id=None):  # noqa: D401
        return None


class TestBitrixEmployeeDTO(TransactionCase):
    """Unit tests for BitrixEmployee.active normalization."""

    def test_active_y_to_true(self):
        emp = BitrixEmployee(user_id=1, full_name='X', active='Y')
        self.assertTrue(emp.active)

    def test_active_n_to_false(self):
        emp = BitrixEmployee(user_id=1, full_name='X', active='N')
        self.assertFalse(emp.active)

    def test_active_default_true(self):
        emp = BitrixEmployee(user_id=1, full_name='X')
        self.assertTrue(emp.active)

    def test_active_empty_to_true(self):
        emp = BitrixEmployee(user_id=1, full_name='X', active='')
        self.assertTrue(emp.active)

    def test_empty_dept_ids_allowed(self):
        emp = BitrixEmployee(user_id=1, full_name='X', active='N')
        self.assertEqual(emp.dept_ids, [])


class TestEmployeeValsBuilder(TransactionCase):
    """Unit tests for _build_employee_vals and _prepare_update_vals."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })
        cls.loader = _TestEmployeeLoader(cls.env, _FakeExtractor([]))

    def test_active_employee_no_active_key(self):
        """Active emp → vals do NOT contain 'active' (rely on Odoo default True)."""
        emp = BitrixEmployee(user_id=1, full_name='A', active='Y')
        vals = self.loader._build_employee_vals(emp)
        self.assertNotIn('active', vals)

    def test_fired_employee_active_false_in_vals(self):
        emp = BitrixEmployee(user_id=1, full_name='A', active='N')
        vals = self.loader._build_employee_vals(emp)
        self.assertIn('active', vals)
        self.assertFalse(vals['active'])

    def test_update_downgrades_true_to_false(self):
        existing = self.env['hr.employee'].create({
            'name': 'X1', 'x_bitrix_id': 990901,
        })
        emp = BitrixEmployee(user_id=990901, full_name='X1', active='N')
        vals = self.loader._build_employee_vals(emp)
        update = self.loader._prepare_update_vals(existing, vals)
        self.assertEqual(update.get('active'), False)

    def test_update_does_not_reactivate(self):
        existing = self.env['hr.employee'].create({
            'name': 'X2', 'x_bitrix_id': 990902,
        })
        existing.write({'active': False})
        existing.invalidate_recordset()

        emp = BitrixEmployee(user_id=990902, full_name='X2', active='Y')
        vals = self.loader._build_employee_vals(emp)
        update = self.loader._prepare_update_vals(existing, vals)
        self.assertNotIn('active', update, 'must not auto re-activate')


class TestFiredEmployeeRolesOnTasks(TransactionCase):
    """A fired employee must still resolve to a responsible link on a task."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_responsible_link_for_fired_employee(self):
        """Link record (role='responsible') is created even when employee is archived."""
        fired = self.env['hr.employee'].create({
            'name': 'Fired Responsible',
            'x_bitrix_id': 990010,
        })
        fired.write({'active': False})

        project = self.env['project.project'].create({'name': 'P fired roles'})
        task = self.env['project.task'].create({
            'name': 'T fired responsible',
            'project_id': project.id,
            'x_bitrix_id': '880001',
        })

        link = self.env['bitrix.task.employee.link'].create({
            'task_id': task.id,
            'employee_id': fired.id,
            'role': 'responsible',
        })
        task.invalidate_recordset()

        self.assertEqual(link.role, 'responsible')
        self.assertEqual(task.x_bitrix_responsible_employee_id, fired)
        self.assertFalse(task.x_bitrix_responsible_employee_id.active)
