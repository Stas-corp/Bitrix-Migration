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

                manager_id = self._resolve_manager(dept.head_user_id)
                vals = {
                    'name': dept.dept_name,
                    'x_bitrix_id': dept.dept_id,
                }
                if manager_id:
                    vals['manager_id'] = manager_id

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

    def _resolve_manager(self, head_user_id):
        """Find hr.employee.id for a Bitrix head_user_id via user mapping."""
        if not head_user_id:
            return None

        partner_id = self.user_map.get(str(head_user_id))
        if not partner_id:
            return None

        # Find res.users by partner_id → then hr.employee by user_id
        user = self.env['res.users'].sudo().search(
            [('partner_id', '=', partner_id)], limit=1
        )
        if user:
            emp = self.env['hr.employee'].sudo().search(
                [('user_id', '=', user.id)], limit=1
            )
            if emp:
                return emp.id

        return None
