"""Tests for hr.employee.job_id / job_title migration from Bitrix WORK_POSITION."""
from odoo.tests.common import TransactionCase

from ..services.loaders.employees import EmployeeLoader
from ..services.normalizers.dto import BitrixEmployee


class _FakeJobExtractor:
    def __init__(self, employees=None, telegrams=None, departments=None):
        self.employees = employees or []
        self.telegrams = telegrams or {}
        self.departments = departments or []

    def get_employees(self):
        return self.employees

    def get_employee_telegrams(self):
        return self.telegrams

    def get_departments(self):
        return self.departments


class _TestEmployeeLoader(EmployeeLoader):
    """EmployeeLoader override that disables commit/checkpoint inside tests."""

    def commit_checkpoint(self, count, last_bitrix_id=None):
        return None

    def link_parents(self):
        # Hierarchy is not the subject of these tests.
        return None


def _emp_row(user_id, work_position=None, active='Y'):
    return {
        'user_id': user_id,
        'login': f'u{user_id}',
        'full_name': f'User {user_id}',
        'email': '',
        'active': active,
        'raw_dept': 'a:1:{i:0;i:1;}',
        'work_phone': None,
        'mobile_phone': None,
        'personal_phone': None,
        'work_position': work_position,
    }


class TestNormalizePosition(TransactionCase):
    """Pure unit tests for whitespace normalization."""

    def test_strip_and_collapse(self):
        self.assertEqual(
            EmployeeLoader._normalize_position('  Senior   Developer  '),
            'Senior Developer',
        )

    def test_empty_returns_none(self):
        self.assertIsNone(EmployeeLoader._normalize_position(''))
        self.assertIsNone(EmployeeLoader._normalize_position(None))
        self.assertIsNone(EmployeeLoader._normalize_position('   '))

    def test_preserves_case(self):
        self.assertEqual(
            EmployeeLoader._normalize_position('Tech Lead'),
            'Tech Lead',
        )


class TestBitrixEmployeeWorkPosition(TransactionCase):
    """DTO parses work_position with same _clean_str semantics."""

    def test_value_kept(self):
        emp = BitrixEmployee(**_emp_row(101, work_position='Developer'))
        self.assertEqual(emp.work_position, 'Developer')

    def test_empty_string_becomes_none(self):
        emp = BitrixEmployee(**_emp_row(102, work_position=''))
        self.assertIsNone(emp.work_position)

    def test_missing_field_defaults_none(self):
        row = _emp_row(103)
        row.pop('work_position')
        emp = BitrixEmployee(**row)
        self.assertIsNone(emp.work_position)


class TestResolveJobId(TransactionCase):
    """_resolve_job_id creates hr.job once and caches by normalized name."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _loader(self):
        return _TestEmployeeLoader(self.env, _FakeJobExtractor())

    def test_creates_job(self):
        loader = self._loader()
        job_id = loader._resolve_job_id('QA Engineer')
        job = self.env['hr.job'].browse(job_id)
        self.assertTrue(job.exists())
        self.assertEqual(job.name, 'QA Engineer')

    def test_reuses_existing_by_name(self):
        existing = self.env['hr.job'].create({'name': 'Manual Position'})
        loader = self._loader()
        self.assertEqual(loader._resolve_job_id('Manual Position'), existing.id)

    def test_caches_per_loader(self):
        loader = self._loader()
        first = loader._resolve_job_id('Backend Dev')
        # Delete record to prove second call uses cache, not DB
        self.env['hr.job'].browse(first).unlink()
        self.assertEqual(loader._resolve_job_id('Backend Dev'), first)

    def test_normalization_dedupes(self):
        loader = self._loader()
        first = loader._resolve_job_id('  Sales  Manager ')
        second = loader._resolve_job_id('Sales Manager')
        self.assertEqual(first, second)

    def test_empty_returns_none(self):
        loader = self._loader()
        self.assertIsNone(loader._resolve_job_id(''))
        self.assertIsNone(loader._resolve_job_id(None))


class TestRunSetsJobFields(TransactionCase):
    """Full EmployeeLoader.run() sets job_id and job_title."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _run(self, rows):
        loader = _TestEmployeeLoader(self.env, _FakeJobExtractor(rows))
        loader.run()
        return loader

    def test_creates_job_and_sets_title(self):
        self._run([_emp_row(700101, work_position='Senior Developer')])
        emp = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 700101)], limit=1,
        )
        self.assertTrue(emp)
        self.assertEqual(emp.job_title, 'Senior Developer')
        self.assertTrue(emp.job_id)
        self.assertEqual(emp.job_id.name, 'Senior Developer')

    def test_empty_position_leaves_fields_unset(self):
        self._run([_emp_row(700102, work_position=None)])
        emp = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 700102)], limit=1,
        )
        self.assertTrue(emp)
        self.assertFalse(emp.job_id)
        self.assertFalse(emp.job_title)

    def test_idempotent_no_duplicate_job(self):
        rows = [
            _emp_row(700201, work_position='Tech Lead'),
            _emp_row(700202, work_position='Tech Lead'),
        ]
        self._run(rows)
        self._run(rows)  # second pass
        jobs = self.env['hr.job'].search([('name', '=', 'Tech Lead')])
        self.assertEqual(len(jobs), 1)


class TestSyncJobTitlesOnly(TransactionCase):
    """sync_job_titles_only() updates only existing employees, no creation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _sync(self, rows):
        loader = _TestEmployeeLoader(self.env, _FakeJobExtractor(rows))
        loader.sync_job_titles_only()
        return loader

    def test_updates_existing_employee(self):
        emp = self.env['hr.employee'].create({
            'name': 'Existing One',
            'x_bitrix_id': 800001,
        })
        self.assertFalse(emp.job_id)

        self._sync([_emp_row(800001, work_position='DevOps Engineer')])

        emp.invalidate_recordset()
        self.assertEqual(emp.job_title, 'DevOps Engineer')
        self.assertEqual(emp.job_id.name, 'DevOps Engineer')

    def test_does_not_create_new_employee(self):
        self._sync([_emp_row(800099, work_position='Phantom Role')])
        missing = self.env['hr.employee'].with_context(active_test=False).search(
            [('x_bitrix_id', '=', 800099)],
        )
        self.assertFalse(missing)

    def test_position_change_propagates(self):
        existing_job = self.env['hr.job'].create({'name': 'Junior Developer'})
        emp = self.env['hr.employee'].create({
            'name': 'Promoted Person',
            'x_bitrix_id': 800002,
            'job_id': existing_job.id,
            'job_title': 'Junior Developer',
        })

        self._sync([_emp_row(800002, work_position='Senior Developer')])

        emp.invalidate_recordset()
        self.assertEqual(emp.job_title, 'Senior Developer')
        self.assertEqual(emp.job_id.name, 'Senior Developer')

    def test_clears_when_work_position_empty(self):
        job = self.env['hr.job'].create({'name': 'Former Title'})
        emp = self.env['hr.employee'].create({
            'name': 'Title Wipe',
            'x_bitrix_id': 800003,
            'job_id': job.id,
            'job_title': 'Former Title',
        })

        self._sync([_emp_row(800003, work_position=None)])

        emp.invalidate_recordset()
        self.assertFalse(emp.job_id)
        self.assertFalse(emp.job_title)

    def test_unchanged_position_no_write(self):
        job = self.env['hr.job'].create({'name': 'Stable Role'})
        emp = self.env['hr.employee'].create({
            'name': 'Steady',
            'x_bitrix_id': 800004,
            'job_id': job.id,
            'job_title': 'Stable Role',
        })
        write_date_before = emp.write_date

        self._sync([_emp_row(800004, work_position='Stable Role')])

        emp.invalidate_recordset()
        self.assertEqual(emp.write_date, write_date_before)
