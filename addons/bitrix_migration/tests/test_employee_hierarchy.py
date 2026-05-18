"""Tests for hr.employee.parent_id derivation from Bitrix UF_HEAD chains."""
from odoo.tests.common import TransactionCase

from ..services.loaders.employees import EmployeeLoader
from ..services.loaders.hierarchy import (
    build_department_tree,
    compute_employee_parent_id,
)


class _FakeDept:
    def __init__(self, dept_id, parent_dept_id=None, head_user_id=None):
        self.dept_id = dept_id
        self.parent_dept_id = parent_dept_id
        self.head_user_id = head_user_id


class _FakeHierarchyExtractor:
    def __init__(self, employees=None, departments=None):
        self.employees = employees or []
        self.departments = departments or []

    def get_employees(self):
        return self.employees

    def get_employee_telegrams(self):
        return {}

    def get_departments(self):
        return self.departments


class _TestEmployeeLoader(EmployeeLoader):
    """EmployeeLoader override that disables commit/checkpoint inside tests."""

    def commit_checkpoint(self, count, last_bitrix_id=None):
        return None


class TestComputeEmployeeParentId(TransactionCase):
    """Pure-function unit tests for compute_employee_parent_id."""

    def test_basic_head_in_same_department(self):
        tree = build_department_tree([_FakeDept(10, None, 500)])
        self.assertEqual(compute_employee_parent_id(99, [10], tree), 500)

    def test_self_is_head_walks_up_to_parent(self):
        """The Артур Кугот case: employee == UF_HEAD of their dept → use parent's head."""
        tree = build_department_tree([
            _FakeDept(10, parent_dept_id=5, head_user_id=2470),
            _FakeDept(5, parent_dept_id=None, head_user_id=900),
        ])
        self.assertEqual(compute_employee_parent_id(2470, [10], tree), 900)

    def test_chain_of_empty_uf_head(self):
        tree = build_department_tree([
            _FakeDept(10, parent_dept_id=5, head_user_id=None),
            _FakeDept(5, parent_dept_id=1, head_user_id=None),
            _FakeDept(1, parent_dept_id=None, head_user_id=999),
        ])
        self.assertEqual(compute_employee_parent_id(42, [10], tree), 999)

    def test_root_head_is_self_returns_none(self):
        tree = build_department_tree([_FakeDept(1, None, 999)])
        self.assertIsNone(compute_employee_parent_id(999, [1], tree))

    def test_chain_ends_without_match(self):
        tree = build_department_tree([
            _FakeDept(10, parent_dept_id=None, head_user_id=42),
        ])
        self.assertIsNone(compute_employee_parent_id(42, [10], tree))

    def test_cycle_in_dept_tree(self):
        tree = {
            10: {'parent_id': 5, 'uf_head': None},
            5: {'parent_id': 10, 'uf_head': None},
        }
        self.assertIsNone(compute_employee_parent_id(7, [10], tree))

    def test_multiple_uf_department_uses_first(self):
        tree = build_department_tree([
            _FakeDept(10, None, 500),
            _FakeDept(20, None, 800),
        ])
        self.assertEqual(compute_employee_parent_id(7, [10, 20], tree), 500)

    def test_empty_dept_ids_returns_none(self):
        self.assertIsNone(compute_employee_parent_id(7, [], {}))

    def test_unknown_dept_id_returns_none(self):
        self.assertIsNone(compute_employee_parent_id(7, [42], {}))

    def test_creates_parent_cycle_helper(self):
        # A.parent = B, attempting B.parent = A → cycle
        parent_lookup = {1: 2}
        self.assertTrue(
            EmployeeLoader._creates_parent_cycle(2, 1, parent_lookup)
        )

    def test_creates_parent_cycle_helper_negative(self):
        parent_lookup = {1: 2, 2: 3}
        self.assertFalse(
            EmployeeLoader._creates_parent_cycle(3, 4, parent_lookup)
        )


class TestLinkParentsIntegration(TransactionCase):
    """End-to-end: link_parents() writes hr.employee.parent_id correctly.

    Creates hr.department / hr.employee directly via ORM to avoid
    DepartmentLoader.run() commits (test sandbox forbids cr.commit).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _emp_row(self, user_id, dept_id, active='Y'):
        return {
            'user_id': user_id,
            'login': f'u{user_id}',
            'full_name': f'User {user_id}',
            'email': '',
            'active': active,
            'raw_dept': f'a:1:{{i:0;i:{dept_id};}}',
            'work_phone': None,
            'mobile_phone': None,
            'personal_phone': None,
        }

    def _dept_row(self, dept_id, parent=None, head=None):
        return {
            'dept_id': dept_id,
            'dept_name': f'Dept {dept_id}',
            'parent_dept_id': parent,
            'head_user_id': head,
            'depth_level': 1,
        }

    def _seed_employees(self, bitrix_ids):
        Employee = self.env['hr.employee']
        for bid in bitrix_ids:
            Employee.create({'name': f'User {bid}', 'x_bitrix_id': bid})

    def _run_link_parents(self, emp_rows, dept_rows):
        loader = _TestEmployeeLoader(
            self.env, _FakeHierarchyExtractor(emp_rows, dept_rows)
        )
        loader.link_parents()
        return loader

    def test_artur_kugot_case(self):
        """UF_HEAD of own dept is self → parent_id resolves to UF_HEAD of parent dept."""
        self._seed_employees([992001, 992002, 992003, 992004])

        dept_rows = [
            self._dept_row(910, parent=None, head=992001),  # Юлія eq
            self._dept_row(911, parent=910, head=992002),
            self._dept_row(912, parent=911, head=992003),
            self._dept_row(913, parent=912, head=992004),  # Артур eq
        ]
        emp_rows = [
            self._emp_row(992001, 910),
            self._emp_row(992002, 911),
            self._emp_row(992003, 912),
            self._emp_row(992004, 913),
        ]

        self._run_link_parents(emp_rows, dept_rows)

        Employee = self.env['hr.employee'].with_context(active_test=False)
        artur = Employee.search([('x_bitrix_id', '=', 992004)], limit=1)
        popov = Employee.search([('x_bitrix_id', '=', 992003)], limit=1)
        tanchenko = Employee.search([('x_bitrix_id', '=', 992002)], limit=1)
        yulia = Employee.search([('x_bitrix_id', '=', 992001)], limit=1)

        self.assertEqual(artur.parent_id, popov)
        self.assertEqual(popov.parent_id, tanchenko)
        self.assertEqual(tanchenko.parent_id, yulia)
        self.assertFalse(yulia.parent_id)

    def test_missing_parent_employee_logs_no_crash(self):
        """UF_HEAD points to a user not in Odoo → parent_id stays empty, no exception."""
        self._seed_employees([993100])

        dept_rows = [self._dept_row(920, parent=None, head=993999)]
        emp_rows = [self._emp_row(993100, 920)]
        self._run_link_parents(emp_rows, dept_rows)

        emp = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 993100)], limit=1,
        )
        self.assertTrue(emp)
        self.assertFalse(emp.parent_id)

    def test_idempotent_rerun(self):
        self._seed_employees([994001, 994002])

        dept_rows = [
            self._dept_row(930, parent=None, head=994001),
            self._dept_row(931, parent=930, head=994002),
        ]
        emp_rows = [
            self._emp_row(994001, 930),
            self._emp_row(994002, 931),
        ]
        self._run_link_parents(emp_rows, dept_rows)
        self._run_link_parents(emp_rows, dept_rows)  # second pass — must not change anything

        emp = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 994002)], limit=1,
        )
        boss = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 994001)], limit=1,
        )
        self.assertEqual(emp.parent_id, boss)

    def test_self_head_walks_up_when_chain_has_gap(self):
        """Mid-level dept has empty UF_HEAD: walk up past the gap to find a manager."""
        self._seed_employees([995001, 995002])

        dept_rows = [
            self._dept_row(940, parent=None, head=995001),
            self._dept_row(941, parent=940, head=None),  # container, no head
            self._dept_row(942, parent=941, head=995002),
        ]
        emp_rows = [
            self._emp_row(995001, 940),
            self._emp_row(995002, 942),
        ]
        self._run_link_parents(emp_rows, dept_rows)

        boss = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 995001)], limit=1,
        )
        emp = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 995002)], limit=1,
        )
        # 995002 is UF_HEAD of dept 942 → walk up to 941 (None) → 940 (995001) → parent = 995001
        self.assertEqual(emp.parent_id, boss)
