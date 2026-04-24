from odoo.tests.common import TransactionCase

from ..services.loaders.departments import DepartmentLoader


class _FakeDepartmentExtractor:
    def __init__(self, rows):
        self.rows = rows

    def get_departments(self):
        return self.rows


class TestDepartmentLoader(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def test_run_recreates_departments_when_mapping_points_to_deleted_record(self):
        stale_department = self.env['hr.department'].create({
            'name': 'Old Sales',
            'x_bitrix_id': 10,
        })
        self.env['bitrix.migration.mapping'].create({
            'bitrix_id': '10',
            'entity_type': 'department',
            'odoo_model': 'hr.department',
            'odoo_id': stale_department.id,
        })
        stale_department.unlink()

        extractor = _FakeDepartmentExtractor([
            {
                'dept_id': 10,
                'dept_name': 'Sales',
                'parent_dept_id': None,
                'head_user_id': None,
                'depth_level': 1,
            },
            {
                'dept_id': 11,
                'dept_name': 'Regional',
                'parent_dept_id': 10,
                'head_user_id': None,
                'depth_level': 2,
            },
        ])

        loader = DepartmentLoader(self.env, extractor=extractor, user_map={})
        loader.run()

        sales = self.env['hr.department'].search([('x_bitrix_id', '=', 10)], limit=1)
        regional = self.env['hr.department'].search([('x_bitrix_id', '=', 11)], limit=1)
        self.assertTrue(sales)
        self.assertTrue(regional)
        self.assertEqual(regional.parent_id, sales)

        mapping = self.env['bitrix.migration.mapping']
        current_map = mapping.get_all_mappings(
            'department', model_name='hr.department', only_existing=True,
        )
        self.assertEqual(current_map.get('10'), sales.id)
        self.assertEqual(current_map.get('11'), regional.id)

    def test_run_restores_mapping_and_sets_manager_by_employee_bitrix_id(self):
        department = self.env['hr.department'].create({
            'name': 'Existing Support',
            'x_bitrix_id': 20,
        })
        manager = self.env['hr.employee'].create({
            'name': 'Support Manager',
            'x_bitrix_id': 700,
        })

        extractor = _FakeDepartmentExtractor([{
            'dept_id': 20,
            'dept_name': 'Support',
            'parent_dept_id': None,
            'head_user_id': 700,
            'depth_level': 1,
        }])

        loader = DepartmentLoader(self.env, extractor=extractor, user_map={})
        loader.run()

        department.invalidate_recordset()
        self.assertEqual(department.manager_id, manager)

        current_map = self.env['bitrix.migration.mapping'].get_all_mappings(
            'department', model_name='hr.department', only_existing=True,
        )
        self.assertEqual(current_map.get('20'), department.id)

    def test_sync_department_managers_after_employee_import(self):
        extractor = _FakeDepartmentExtractor([{
            'dept_id': 30,
            'dept_name': 'Operations',
            'parent_dept_id': None,
            'head_user_id': 701,
            'depth_level': 1,
        }])

        loader = DepartmentLoader(self.env, extractor=extractor, user_map={})
        loader.run()

        department = self.env['hr.department'].search([('x_bitrix_id', '=', 30)], limit=1)
        self.assertTrue(department)
        self.assertFalse(department.manager_id)

        manager = self.env['hr.employee'].create({
            'name': 'Operations Manager',
            'x_bitrix_id': 701,
        })

        loader.sync_department_managers()

        department.invalidate_recordset()
        self.assertEqual(department.manager_id, manager)

    def test_empty_head_does_not_clear_existing_manager(self):
        manager = self.env['hr.employee'].create({
            'name': 'Manual Manager',
            'x_bitrix_id': 702,
        })
        department = self.env['hr.department'].create({
            'name': 'Manual Department',
            'x_bitrix_id': 40,
            'manager_id': manager.id,
        })

        extractor = _FakeDepartmentExtractor([{
            'dept_id': 40,
            'dept_name': 'Manual Department',
            'parent_dept_id': None,
            'head_user_id': None,
            'depth_level': 1,
        }])

        loader = DepartmentLoader(self.env, extractor=extractor, user_map={})
        loader.run()

        department.invalidate_recordset()
        self.assertEqual(department.manager_id, manager)
