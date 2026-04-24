import logging

from .base import BaseLoader

_logger = logging.getLogger(__name__)


class DepartmentLoader(BaseLoader):
    """Loads Bitrix department hierarchy into hr.department.

    Two-pass approach:
      Pass 1 — create all departments without parent_id (sorted by DEPTH_LEVEL)
      Pass 2 — link parent_id using the mapping built in pass 1
    """

    entity_type = 'department'
    batch_size = 200

    def __init__(self, env, extractor, user_map=None, **kwargs):
        super().__init__(env, extractor, **kwargs)
        # user_map: {str(bitrix_user_id): odoo_partner_id}
        # Kept for backwards-compatible constructor calls. Department managers
        # are resolved directly by hr.employee.x_bitrix_id.
        self.user_map = user_map or {}

    def run(self):
        self.log('Extracting Bitrix departments...')
        rows = self.extractor.get_departments()
        self.log(f'Found {len(rows)} departments')

        if not rows:
            return

        from ...services.normalizers.dto import BitrixDepartment

        depts = []
        for row in rows:
            try:
                depts.append(BitrixDepartment(**row))
            except Exception as e:
                self.log(f'ERROR parsing department row {row}: {e}')

        mapping = self.get_mapping()
        removed_stale = 0
        if not self.dry_run:
            removed_stale = mapping.purge_stale_mappings('department', 'hr.department')
        existing = mapping.get_all_mappings(
            'department', model_name='hr.department', only_existing=True,
        )
        existing = self._restore_department_mappings(depts, existing)

        if removed_stale:
            self.log(f'Removed {removed_stale} stale department mappings before import')

        # ── Pass 1: create departments (no parent_id yet) ─────────────
        self.log('Pass 1: creating departments...')
        processed = 0
        for batch in self._batched(depts, self.batch_size):
            for dept in batch:
                bid = str(dept.dept_id)
                if bid in existing:
                    self.skipped_count += 1
                    processed += 1
                    continue

                manager = self._resolve_manager(dept.head_user_id)
                vals = {
                    'name': dept.dept_name,
                    'x_bitrix_id': dept.dept_id,
                }
                if manager:
                    vals['manager_id'] = manager.id

                record, created = self.get_or_create(
                    'hr.department',
                    [('x_bitrix_id', '=', dept.dept_id)],
                    vals,
                    bitrix_id=dept.dept_id,
                    entity_type='department',
                )
                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()

        # ── Pass 2: link parent_id ────────────────────────────────────
        self.log('Pass 2: linking parent departments...')
        fresh_mapping = mapping.get_all_mappings(
            'department', model_name='hr.department', only_existing=True,
        )
        Department = self.env['hr.department'].sudo().with_context(active_test=False)
        linked = 0
        errors = 0

        for dept in depts:
            if not dept.parent_dept_id:
                continue

            child_odoo_id = fresh_mapping.get(str(dept.dept_id))
            parent_odoo_id = fresh_mapping.get(str(dept.parent_dept_id))

            if not child_odoo_id or not parent_odoo_id:
                errors += 1
                continue

            if not self.dry_run:
                try:
                    child = Department.browse(child_odoo_id).exists()
                    parent = Department.browse(parent_odoo_id).exists()
                    if not child or not parent:
                        errors += 1
                        continue

                    if child.parent_id.id != parent.id:
                        child.write({'parent_id': parent.id})
                    linked += 1
                except Exception as e:
                    errors += 1
                    self.log(f'ERROR linking dept {dept.dept_id} → parent {dept.parent_dept_id}: {e}')

        if not self.dry_run:
            self.env.cr.commit()

        self.log(f'Pass 2 done: linked={linked}, errors={errors}')
        self.sync_department_managers(depts)

    def _restore_department_mappings(self, depts, existing=None):
        """Restore missing department mappings from hr.department.x_bitrix_id."""
        existing = dict(existing or {})
        if self.dry_run:
            return existing

        Department = self.env['hr.department'].sudo().with_context(active_test=False)
        mapping = self.get_mapping()
        restored = 0

        for dept in depts:
            bid = str(dept.dept_id)
            if bid in existing:
                continue

            department = Department.search([('x_bitrix_id', '=', dept.dept_id)], limit=1)
            if not department:
                continue

            mapping.set_mapping(bid, 'department', 'hr.department', department.id)
            existing[bid] = department.id
            restored += 1

        if restored:
            self.env.cr.commit()
            self.log(f'Restored department mappings from x_bitrix_id: {restored}')

        return existing

    def _normalize_departments(self, rows):
        from ...services.normalizers.dto import BitrixDepartment

        depts = []
        for row in rows:
            try:
                depts.append(BitrixDepartment(**row))
            except Exception as e:
                self.log(f'ERROR parsing department row {row}: {e}')
        return depts

    def sync_department_managers(self, depts=None):
        """Backfill hr.department.manager_id from Bitrix department UF_HEAD."""
        if depts is None:
            self.log('Extracting Bitrix departments for manager sync...')
            rows = self.extractor.get_departments()
            self.log(f'Found {len(rows)} departments')
            depts = self._normalize_departments(rows)

        if not depts:
            return

        mapping = self.get_mapping()
        existing = mapping.get_all_mappings(
            'department', model_name='hr.department', only_existing=True,
        )
        existing = self._restore_department_mappings(depts, existing)

        Department = self.env['hr.department'].sudo().with_context(active_test=False)

        updated = 0
        unchanged = 0
        skipped_no_head = 0
        missing_department = 0
        missing_manager = 0

        for dept in depts:
            if not dept.head_user_id:
                skipped_no_head += 1
                continue

            department = self._resolve_department(dept.dept_id, existing, Department)
            if not department:
                missing_department += 1
                self.log(
                    f'WARNING department bitrix_id={dept.dept_id}: '
                    'cannot set manager, department not found'
                )
                continue

            manager = self._resolve_manager(dept.head_user_id)
            if not manager:
                missing_manager += 1
                self.log(
                    f'WARNING department bitrix_id={dept.dept_id}: '
                    f'cannot resolve manager employee bitrix_id={dept.head_user_id}'
                )
                continue

            if department.manager_id.id == manager.id:
                unchanged += 1
                continue

            if not self.dry_run:
                department.write({'manager_id': manager.id})
            updated += 1

        if not self.dry_run:
            self.env.cr.commit()

        self.log(
            'Department managers sync done: '
            f'updated={updated}, unchanged={unchanged}, '
            f'skipped_no_head={skipped_no_head}, '
            f'missing_department={missing_department}, '
            f'missing_manager={missing_manager}'
        )

    def _resolve_department(self, bitrix_dept_id, department_map, Department):
        odoo_id = department_map.get(str(bitrix_dept_id))
        if odoo_id:
            department = Department.browse(odoo_id).exists()
            if department:
                return department

        return Department.search([('x_bitrix_id', '=', bitrix_dept_id)], limit=1)

    def _resolve_manager(self, head_user_id):
        """Find hr.employee for a Bitrix department head user id."""
        if not head_user_id:
            return self.env['hr.employee']

        return self.env['hr.employee'].sudo().with_context(active_test=False).search(
            [('x_bitrix_id', '=', int(head_user_id))], limit=1,
        )
