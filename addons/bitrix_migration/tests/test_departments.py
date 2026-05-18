from odoo.tests.common import TransactionCase

from ..services.loaders.departments import DepartmentLoader


class _FakeDepartmentExtractor:
    def __init__(self, rows):
        self.rows = rows

    def get_departments(self):
        return self.rows


class _TestDepartmentLoader(DepartmentLoader):
    """DepartmentLoader override: no commit/checkpoint inside a test transaction."""

    def commit_checkpoint(self, count, last_bitrix_id=None):
        return None


class TestDepartmentLoader(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _make_inherit_loader(self):
        return _TestDepartmentLoader(self.env, _FakeDepartmentExtractor([]))

    def test_manager_inherited_from_parent_when_no_uf_head(self):
        from ..services.normalizers.dto import BitrixDepartment

        root_manager = self.env['hr.employee'].create({
            'name': 'Root Boss',
            'x_bitrix_id': 800,
        })
        Department = self.env['hr.department']
        root_dept = Department.create({
            'name': 'Root',
            'x_bitrix_id': 50,
            'manager_id': root_manager.id,
        })
        container = Department.create({
            'name': 'Container',
            'x_bitrix_id': 51,
            'parent_id': root_dept.id,
        })

        depts = [
            BitrixDepartment(dept_id=50, dept_name='Root', parent_dept_id=None,
                             head_user_id=800, depth_level=1),
            BitrixDepartment(dept_id=51, dept_name='Container', parent_dept_id=50,
                             head_user_id=None, depth_level=2),
        ]
        self._make_inherit_loader().inherit_managers_from_parents(depts)

        container.invalidate_recordset()
        self.assertEqual(container.manager_id, root_manager)

    def test_inherited_manager_does_not_overwrite_existing(self):
        from ..services.normalizers.dto import BitrixDepartment

        existing_manager = self.env['hr.employee'].create({
            'name': 'Manual Override',
            'x_bitrix_id': 801,
        })
        root_manager = self.env['hr.employee'].create({
            'name': 'Root Boss 2',
            'x_bitrix_id': 802,
        })
        Department = self.env['hr.department']
        root_dept = Department.create({
            'name': 'Root 2',
            'x_bitrix_id': 60,
            'manager_id': root_manager.id,
        })
        container = Department.create({
            'name': 'Pre-existing Container',
            'x_bitrix_id': 61,
            'parent_id': root_dept.id,
            'manager_id': existing_manager.id,
        })

        depts = [
            BitrixDepartment(dept_id=60, dept_name='Root 2', parent_dept_id=None,
                             head_user_id=802, depth_level=1),
            BitrixDepartment(dept_id=61, dept_name='Pre-existing Container',
                             parent_dept_id=60, head_user_id=None, depth_level=2),
        ]
        self._make_inherit_loader().inherit_managers_from_parents(depts)

        container.invalidate_recordset()
        self.assertEqual(container.manager_id, existing_manager)
        self.assertNotEqual(container.manager_id, root_manager)

