import logging

from .base import BaseLoader

_logger = logging.getLogger(__name__)


class EmployeeLoader(BaseLoader):
    """Creates or updates hr.employee records for Bitrix employees."""

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

        # Telegram is loaded separately and is optional across Bitrix versions.
        self.log('Fetching Telegram accounts...')
        telegram_map = self.extractor.get_employee_telegrams()
        self.log(f'Found {len(telegram_map)} Telegram accounts')

        from ...services.normalizers.dto import BitrixEmployee

        employees = []
        for row in rows:
            try:
                employees.append(BitrixEmployee(**row))
            except Exception as e:
                self.log(f'ERROR parsing employee row {row}: {e}')

        mapping = self.get_mapping()
        existing = mapping.get_all_mappings('employee')
        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)

        processed = 0
        for batch in self._batched(employees, self.batch_size):
            for emp in batch:
                bid = str(emp.user_id)
                dept_id = self._resolve_dept(emp.dept_ids)
                odoo_user_id = self._resolve_user(emp.user_id)
                vals = self._build_employee_vals(
                    emp,
                    dept_id=dept_id,
                    odoo_user_id=odoo_user_id,
                    telegram=telegram_map.get(bid),
                )

                employee = None
                mapped_odoo_id = existing.get(bid)
                if mapped_odoo_id:
                    employee = Employee.browse(mapped_odoo_id).exists()

                if not employee:
                    employee = Employee.search([('x_bitrix_id', '=', emp.user_id)], limit=1)
                    if employee:
                        existing[bid] = employee.id
                        if not self.dry_run:
                            mapping.set_mapping(
                                bid, 'employee', 'hr.employee', employee.id,
                            )

                if employee:
                    self._update_employee(employee, emp.user_id, vals)
                else:
                    record, created = self.get_or_create(
                        'hr.employee',
                        [('x_bitrix_id', '=', emp.user_id)],
                        vals,
                        bitrix_id=emp.user_id,
                        entity_type='employee',
                    )
                    if record:
                        employee = record
                    if created and record:
                        existing[bid] = record.id

                if employee and not self.dry_run:
                    self._sync_related_records(employee)

                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()

    def _build_employee_vals(self, emp, dept_id=None, odoo_user_id=None, telegram=None):
        """Map Bitrix employee contacts to the closest Odoo employee fields."""
        vals = {
            'name': emp.full_name,
            'work_email': emp.email or '',
            'work_phone': emp.work_phone or '',
            'mobile_phone': emp.mobile_phone or emp.personal_phone or '',
            'x_bitrix_id': emp.user_id,
        }

        telegram = (telegram or '').strip()
        if telegram:
            vals['x_bitrix_telegram'] = telegram
        if dept_id:
            vals['department_id'] = dept_id
        if odoo_user_id:
            vals['user_id'] = odoo_user_id

        return vals

    def _prepare_update_vals(self, employee, vals):
        """Keep reruns safe: fill missing data and sync changed source values."""
        update_vals = {}

        for field_name, value in vals.items():
            if value in (None, False, ''):
                continue

            if field_name in ('department_id', 'user_id'):
                current_value = employee[field_name].id if employee[field_name] else False
            else:
                current_value = employee[field_name] or False

            if current_value != value:
                update_vals[field_name] = value

        return update_vals

    def _update_employee(self, employee, bitrix_id, vals):
        update_vals = self._prepare_update_vals(employee, vals)
        if not update_vals:
            self.skipped_count += 1
            return

        if self.dry_run:
            self.updated_count += 1
            return

        try:
            employee.write(update_vals)
            self.updated_count += 1
        except Exception as e:
            self.error_count += 1
            self.errors.append((bitrix_id, str(e)))
            self.log(f'ERROR updating hr.employee bitrix_id={bitrix_id}: {e}')

    def _sync_related_records(self, employee):
        """Relink migrated history and task assignees once employee links exist."""
        partner = self.get_partner_from_employee(employee)
        if partner:
            self._sync_comment_authors(employee, partner.id)

        user = self.get_user_from_employee(employee)
        if user:
            self._sync_task_assignees(employee, user.id)

    def _sync_comment_authors(self, employee, partner_id):
        if not self.db_column_exists('mail_message', 'x_bitrix_author_employee_id'):
            return

        Message = self.env['mail.message'].sudo().with_context(active_test=False)
        messages = Message.search([
            ('x_bitrix_author_employee_id', '=', employee.id),
            ('x_bitrix_author_id', '!=', False),
        ])
        for message in messages:
            message.write({
                'author_id': partner_id,
                'x_bitrix_author_id': False,
            })

    def _sync_task_assignees(self, employee, user_id):
        Task = self.env['project.task'].sudo().with_context(active_test=False)
        if 'x_bitrix_responsible_employee_ids' not in Task._fields:
            return
        if not self.db_table_exists('project_task_bitrix_employee_rel'):
            return

        tasks = Task.search([('x_bitrix_responsible_employee_ids', 'in', employee.id)])
        for task in tasks:
            target_user_ids = []
            for responsible_employee in task.x_bitrix_responsible_employee_ids:
                responsible_user = self.get_user_from_employee(responsible_employee)
                if responsible_user and responsible_user.id not in target_user_ids:
                    target_user_ids.append(responsible_user.id)

            if 'user_ids' in task._fields:
                if set(task.user_ids.ids) != set(target_user_ids):
                    task.write({'user_ids': [(6, 0, target_user_ids)]})
            elif 'user_id' in task._fields:
                target_user_id = target_user_ids[0] if target_user_ids else False
                if (task.user_id.id if task.user_id else False) != target_user_id:
                    task.write({'user_id': target_user_id})

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
