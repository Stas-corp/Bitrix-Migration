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


class TestFiredEmployeeLoader(TransactionCase):
    """End-to-end: fired Bitrix users land as hr.employee active=False."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _run(self, rows):
        loader = _TestEmployeeLoader(self.env, _FakeExtractor(rows))
        loader.run()
        return loader

    def test_fired_employee_created_archived(self):
        """ACTIVE='N' row → hr.employee with active=False and auto work_contact_id."""
        self._run([{
            'user_id': 990001,
            'login': 'fired_user',
            'full_name': 'Fired User',
            'email': '',
            'active': 'N',
            'raw_dept': None,
            'work_phone': None,
            'mobile_phone': None,
            'personal_phone': None,
        }])

        emp = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 990001)], limit=1,
        )
        self.assertTrue(emp)
        self.assertFalse(emp.active)
        # Odoo auto-creates work_contact_id for new hr.employee.
        self.assertTrue(emp.work_contact_id)
        # No res.users auto-link for fired employees.
        self.assertFalse(emp.user_id)

    def test_active_employee_created_active(self):
        self._run([{
            'user_id': 990002,
            'login': 'active_user',
            'full_name': 'Active User',
            'email': '',
            'active': 'Y',
            'raw_dept': None,
            'work_phone': None,
            'mobile_phone': None,
            'personal_phone': None,
        }])

        emp = self.env['hr.employee'].search(
            [('x_bitrix_id', '=', 990002)], limit=1,
        )
        self.assertTrue(emp)
        self.assertTrue(emp.active)

    def test_downgrade_active_true_to_false(self):
        """Existing active employee gets archived on rerun with ACTIVE='N'."""
        emp = self.env['hr.employee'].create({
            'name': 'Will Be Fired',
            'x_bitrix_id': 990003,
        })
        self.assertTrue(emp.active)

        self._run([{
            'user_id': 990003,
            'login': 'will_be_fired',
            'full_name': 'Will Be Fired',
            'email': '',
            'active': 'N',
            'raw_dept': None,
            'work_phone': None,
            'mobile_phone': None,
            'personal_phone': None,
        }])

        emp.invalidate_recordset()
        self.assertFalse(emp.active)

    def test_no_reactivate_false_to_true(self):
        """Manually archived employee is NOT auto-re-enabled on rerun with ACTIVE='Y'."""
        emp = self.env['hr.employee'].create({
            'name': 'Manually Archived',
            'x_bitrix_id': 990004,
        })
        emp.write({'active': False})
        emp.invalidate_recordset()
        self.assertFalse(emp.active)

        self._run([{
            'user_id': 990004,
            'login': 'manual',
            'full_name': 'Manually Archived',
            'email': '',
            'active': 'Y',
            'raw_dept': None,
            'work_phone': None,
            'mobile_phone': None,
            'personal_phone': None,
        }])

        emp.invalidate_recordset()
        self.assertFalse(emp.active, 'manually archived employee must stay archived')


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
