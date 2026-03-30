import logging

from .base import BaseLoader

_logger = logging.getLogger(__name__)


class EmployeeLoader(BaseLoader):
    """Creates hr.employee records for active Bitrix users who have a department.

    Links each employee to:
      - hr.department (via dept_map)
      - res.users (via user_map → partner_id → res.users)
    """

    entity_type = 'employee'
    batch_size = 200

    def __init__(self, env, extractor, user_map=None, dept_map=None, **kwargs):
        super().__init__(env, extractor, **kwargs)
        # user_map: {str(bitrix_user_id): odoo_partner_id}
        self.user_map = user_map or {}
        # dept_map: {str(bitrix_dept_id): odoo_dept_id}
        self.dept_map = dept_map or {}

    def run(self):
        self.log('Extracting Bitrix employees...')
        rows = self.extractor.get_employees()
        self.log(f'Found {len(rows)} active employees with department')

        if not rows:
            return

        from ...services.normalizers.dto import BitrixEmployee

        employees = []
        for row in rows:
            try:
                employees.append(BitrixEmployee(**row))
            except Exception as e:
                self.log(f'ERROR parsing employee row {row}: {e}')

        mapping = self.get_mapping()
        existing = mapping.get_all_mappings('employee')

        processed = 0
        for batch in self._batched(employees, self.batch_size):
            for emp in batch:
                bid = str(emp.user_id)
                if bid in existing:
                    self.skipped_count += 1
                    processed += 1
                    continue

                dept_id = self._resolve_dept(emp.dept_ids)
                odoo_user_id = self._resolve_user(emp.user_id)

                vals = {
                    'name': emp.full_name,
                    'work_email': emp.email or '',
                    'x_bitrix_id': emp.user_id,
                }
                if dept_id:
                    vals['department_id'] = dept_id
                if odoo_user_id:
                    vals['user_id'] = odoo_user_id

                self.get_or_create(
                    'hr.employee',
                    [('x_bitrix_id', '=', emp.user_id)],
                    vals,
                    bitrix_id=emp.user_id,
                    entity_type='employee',
                )
                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()

    def _resolve_dept(self, dept_ids):
        """Return Odoo hr.department id for first known dept_id."""
        for did in dept_ids:
            odoo_id = self.dept_map.get(str(did))
            if odoo_id:
                return odoo_id
        return None

    def _resolve_user(self, bitrix_user_id):
        """Return res.users.id for a Bitrix user_id via partner mapping."""
        partner_id = self.user_map.get(str(bitrix_user_id))
        if not partner_id:
            return None
        user = self.env['res.users'].sudo().search(
            [('partner_id', '=', partner_id)], limit=1
        )
        return user.id if user else None
