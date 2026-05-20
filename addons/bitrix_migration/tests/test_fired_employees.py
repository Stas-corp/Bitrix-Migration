"""Tests for fired (ACTIVE='N') employee import and end-to-end visibility on tasks."""
from odoo.tests.common import TransactionCase

from ..services.loaders.employees import EmployeeLoader
from ..services.normalizers.dto import BitrixEmployee


class _FakeExtractor:
    """Minimal extractor stub: serves a static set of employee rows."""

    def __init__(self, rows, fired_ids=None):
        self.rows = rows
        self.fired_ids = fired_ids or []

    def get_employees(self):
        return self.rows

    def get_employee_telegrams(self):
        return {}

    def get_fired_employee_ids(self):
        return [{'user_id': uid} for uid in self.fired_ids]


class _TestEmployeeLoader(EmployeeLoader):
    """EmployeeLoader override: no commit/checkpoint and skip hierarchy step.

    ``link_parents`` calls ``extractor.get_departments`` which our stub does
    not implement — it is unrelated to the fired-employee contract, so we
    short-circuit it for these tests.
    """

    def commit_checkpoint(self, count, last_bitrix_id=None):  # noqa: D401
        return None

    def link_parents(self):
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

    def test_fired_employee_NOT_marked_inactive_at_creation(self):
        """Fired emp → vals also do NOT contain 'active'.

        Fired employees must be created as active=True so they survive into
        project.task.user_ids during the main migration. Archival is a
        separate, terminal step (archive_employees mode).
        """
        emp = BitrixEmployee(user_id=1, full_name='A', active='N')
        vals = self.loader._build_employee_vals(emp)
        self.assertNotIn('active', vals)

    def test_update_does_not_reactivate(self):
        """Re-run safety: a manually archived employee must not be re-enabled.

        With the new contract, _build_employee_vals does not emit 'active' at
        all, so _prepare_update_vals never sees a True→True override. This
        test still guards the legacy code path: even if 'active': True ever
        appears in vals (e.g. from a future caller), it must be ignored.
        """
        existing = self.env['hr.employee'].create({
            'name': 'X2', 'x_bitrix_id': 990902,
        })
        existing.write({'active': False})
        existing.invalidate_recordset()

        emp = BitrixEmployee(user_id=990902, full_name='X2', active='Y')
        vals = self.loader._build_employee_vals(emp)
        update = self.loader._prepare_update_vals(existing, vals)
        self.assertNotIn('active', update, 'must not auto re-activate')


class TestEmployeeLoaderRunWithFired(TransactionCase):
    """Integration: EmployeeLoader.run() creates fired employees as active=True."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_fired_employee_created_active(self):
        rows = [{
            'user_id': 990800,
            'full_name': 'Fired RunTest',
            'name': 'Fired',
            'last_name': 'RunTest',
            'email': '',
            'active': 'N',
            'dept_ids': '',
            'work_position': '',
            'work_phone': '',
            'personal_phone': '',
            'mobile_phone': '',
            'personal_photo': None,
        }]
        loader = _TestEmployeeLoader(self.env, _FakeExtractor(rows))
        loader.run()

        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)
        emp = Employee.search([('x_bitrix_id', '=', 990800)], limit=1)
        self.assertTrue(emp, 'employee must be created')
        self.assertTrue(
            emp.active,
            'fired Bitrix user must be created as active=True; '
            'archival is done by archive_fired() at the end of migration',
        )


class TestArchiveFired(TransactionCase):
    """archive_fired() is the only path that flips active=True → False."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_downgrades_active_employee_to_inactive(self):
        """An active hr.employee whose Bitrix user is ACTIVE='N' gets archived."""
        emp = self.env['hr.employee'].create({
            'name': 'ToArchive', 'x_bitrix_id': 990901,
        })
        self.assertTrue(emp.active)

        loader = _TestEmployeeLoader(
            self.env, _FakeExtractor([], fired_ids=[990901]),
        )
        loader.archive_fired()

        emp.invalidate_recordset()
        emp = self.env['hr.employee'].sudo().with_context(
            active_test=False,
        ).browse(emp.id)
        self.assertFalse(emp.active, 'archive_fired() must set active=False')

    def test_idempotent_on_already_archived(self):
        """archive_fired() is a no-op on already archived employees (no error)."""
        emp = self.env['hr.employee'].create({
            'name': 'AlreadyArchived', 'x_bitrix_id': 990902,
        })
        emp.write({'active': False})

        loader = _TestEmployeeLoader(
            self.env, _FakeExtractor([], fired_ids=[990902]),
        )
        loader.archive_fired()  # must not raise

        emp.invalidate_recordset()
        emp = self.env['hr.employee'].sudo().with_context(
            active_test=False,
        ).browse(emp.id)
        self.assertFalse(emp.active)


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


class TestFiredEmployeeEndToEnd(TransactionCase):
    """End-to-end: fired employee stays visible in task.user_ids after archival.

    Reproduces the Bitrix→Odoo flow for fired users:
      1. Employee + linked res.users are created active.
      2. The user is added to a task's user_ids.
      3. archive_fired() archives both hr.employee and res.users.
      4. The user_id stays inside task.user_ids (Many2many keeps the FK).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_fired_user_remains_in_task_user_ids_after_archive(self):
        partner = self.env['res.partner'].create({'name': 'Fired E2E'})
        user = self.env['res.users'].create({
            'name': 'Fired E2E',
            'login': 'fired_e2e_990700',
            'partner_id': partner.id,
        })
        emp = self.env['hr.employee'].create({
            'name': 'Fired E2E',
            'x_bitrix_id': 990700,
            'user_id': user.id,
        })

        project = self.env['project.project'].create({'name': 'P e2e fired'})
        task = self.env['project.task'].create({
            'name': 'T e2e fired',
            'project_id': project.id,
            'x_bitrix_id': '880700',
            'user_ids': [(6, 0, [user.id])],
        })
        self.assertIn(user.id, task.user_ids.ids)

        loader = _TestEmployeeLoader(
            self.env, _FakeExtractor([], fired_ids=[990700]),
        )
        loader.archive_fired()

        emp.invalidate_recordset()
        user.invalidate_recordset()
        task.invalidate_recordset()

        emp = self.env['hr.employee'].sudo().with_context(
            active_test=False,
        ).browse(emp.id)
        user = self.env['res.users'].sudo().with_context(
            active_test=False,
        ).browse(user.id)
        task = self.env['project.task'].sudo().browse(task.id)

        self.assertFalse(emp.active, 'employee must be archived')
        self.assertFalse(user.active, 'res.users must be archived')
        self.assertIn(
            user.id, task.user_ids.ids,
            'archived user must remain in task.user_ids (Many2many keeps FK)',
        )
