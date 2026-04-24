from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProjectTask(models.Model):
    _inherit = 'project.task'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
    x_bitrix_stage_id = fields.Char(string='Bitrix Stage ID', copy=False)
    x_bitrix_parent_id = fields.Char(string='Bitrix Parent ID', copy=False)
    x_bitrix_status_code = fields.Integer(string='Bitrix Status Code', copy=False)
    x_bitrix_created_at = fields.Datetime(string='Bitrix Created At', copy=False)
    x_bitrix_responsible_employee_id = fields.Many2one(
        'hr.employee',
        string='Bitrix Responsible',
        compute='_compute_bitrix_responsible_employee_id',
        inverse='_inverse_bitrix_responsible_employee_id',
        search='_search_bitrix_responsible_employee_id',
        copy=False,
        help='Canonical responsible employee (Bitrix TYPE=R). Single value.',
    )
    x_bitrix_responsible_employee_ids = fields.Many2many(
        'hr.employee',
        string='Bitrix Responsible (deprecated)',
        compute='_compute_bitrix_responsible_employee_ids',
        search='_search_bitrix_responsible_employee_ids',
        copy=False,
        help='Deprecated readonly mirror of x_bitrix_responsible_employee_id.',
    )
    x_bitrix_responsible_user_id = fields.Many2one(
        'res.users',
        string='Bitrix Responsible (User)',
        compute='_compute_bitrix_responsible_user_id',
        inverse='_inverse_bitrix_responsible_user_id',
        copy=False,
        help='User-facing responsible role. Requires a linked employee record.',
    )
    x_bitrix_accomplice_employee_ids = fields.Many2many(
        'hr.employee',
        string='Bitrix Accomplices',
        compute='_compute_bitrix_accomplice_employee_ids',
        inverse='_inverse_bitrix_accomplice_employee_ids',
        search='_search_bitrix_accomplice_employee_ids',
        copy=False,
    )
    x_bitrix_auditor_employee_ids = fields.Many2many(
        'hr.employee',
        string='Bitrix Auditors',
        compute='_compute_bitrix_auditor_employee_ids',
        inverse='_inverse_bitrix_auditor_employee_ids',
        search='_search_bitrix_auditor_employee_ids',
        copy=False,
    )
    x_bitrix_auditor_user_ids = fields.Many2many(
        'res.users',
        compute='_compute_bitrix_auditor_user_ids',
        inverse='_inverse_bitrix_auditor_user_ids',
        string='Bitrix Auditors (Users)',
        copy=False,
        help='User-facing auditor/watcher role. Requires linked employee records.',
    )
    x_bitrix_originator_employee_id = fields.Many2one(
        'hr.employee',
        string='Bitrix Originator',
        compute='_compute_bitrix_originator_employee_id',
        inverse='_inverse_bitrix_originator_employee_id',
        search='_search_bitrix_originator_employee_id',
        copy=False,
        help='Employee who set/assigned this task in Bitrix (role O).',
    )
    x_bitrix_creator_employee_id = fields.Many2one(
        'hr.employee',
        string='Bitrix Creator (Employee)',
        copy=False,
        help='Employee who created this task in Bitrix.',
    )
    x_bitrix_assignee_user_ids = fields.Many2many(
        'res.users',
        related='user_ids',
        readonly=False,
        string='Bitrix Assignees (Users)',
        copy=False,
        help='Canonical set of Odoo users resolved from Bitrix R + A roles. '
             'Includes users resolved via hr.employee and user_map fallback. '
             'user_ids is kept as a mirror of this field.',
    )
    x_bitrix_access_user_ids = fields.Many2many(
        'res.users',
        compute='_compute_bitrix_access_user_ids',
        string='Bitrix Access Users',
        copy=False,
        help='Users who should be able to see and comment on the task: '
             'assignees + auditors + originator + creator.',
    )
    x_bitrix_participant_employee_ids = fields.Many2many(
        'hr.employee',
        string='Bitrix Participants',
        compute='_compute_bitrix_participant_employee_ids',
        inverse='_inverse_bitrix_participant_employee_ids',
        search='_search_bitrix_participant_employee_ids',
        copy=False,
        help='Legacy field. All Bitrix task participants.',
    )

    # ── Compute helpers ──────────────────────────────────────────────

    def _compute_bitrix_employee_ids(self, role, field_name):
        links = self.env['bitrix.task.employee.link'].sudo().search([
            ('task_id', 'in', self.ids),
            ('role', '=', role),
        ])
        by_task = defaultdict(set)
        for link in links:
            by_task[link.task_id.id].add(link.employee_id.id)
        for task in self:
            task[field_name] = [(6, 0, sorted(by_task.get(task.id, set())))]

    def _compute_bitrix_m2o_employee_id(self, role, field_name):
        links = self.env['bitrix.task.employee.link'].sudo().search([
            ('task_id', 'in', self.ids),
            ('role', '=', role),
        ])
        by_task = {}
        for link in links:
            by_task.setdefault(link.task_id.id, link.employee_id.id)
        for task in self:
            task[field_name] = by_task.get(task.id, False)

    def _get_user_from_employee(self, employee):
        if not employee:
            return self.env['res.users']

        if 'user_id' in employee._fields and employee.user_id:
            return employee.user_id

        if 'work_contact_id' in employee._fields and employee.work_contact_id:
            return self.env['res.users'].sudo().search(
                [('partner_id', '=', employee.work_contact_id.id)], limit=1,
            )
        return self.env['res.users']

    def _get_users_from_employees(self, employees):
        users = self.env['res.users']
        for employee in employees:
            user = self._get_user_from_employee(employee)
            if user:
                users |= user
        return users

    def _get_employee_from_user(self, user):
        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)
        if not user:
            return Employee

        employee = Employee.search([('user_id', '=', user.id)], limit=1)
        if employee:
            return employee

        if user.partner_id and 'work_contact_id' in Employee._fields:
            return Employee.search([('work_contact_id', '=', user.partner_id.id)], limit=1)
        return Employee

    def _get_employees_from_users(self, users, role_label):
        employees = self.env['hr.employee']
        missing_users = []
        for user in users:
            employee = self._get_employee_from_user(user)
            if employee:
                employees |= employee
            else:
                missing_users.append(user.display_name)

        if missing_users:
            names = ', '.join(sorted(missing_users))
            raise ValidationError(
                f'Users selected for "{role_label}" must be linked to employees: {names}'
            )
        return employees

    def _set_role_employee_ids(self, task, role, target_employee_ids):
        Link = self.env['bitrix.task.employee.link'].sudo()
        existing_links = Link.search([
            ('task_id', '=', task.id),
            ('role', '=', role),
        ])
        existing_ids = set(existing_links.mapped('employee_id').ids)
        target_ids = set(target_employee_ids)

        to_remove = sorted(existing_ids - target_ids)
        to_add = sorted(target_ids - existing_ids)

        if to_remove:
            Link.search([
                ('task_id', '=', task.id),
                ('role', '=', role),
                ('employee_id', 'in', to_remove),
            ]).unlink()

        if to_add:
            Link.create([
                {
                    'task_id': task.id,
                    'employee_id': employee_id,
                    'role': role,
                }
                for employee_id in to_add
            ])

    def _set_role_employee_id(self, task, role, target_employee_id):
        Link = self.env['bitrix.task.employee.link'].sudo()
        existing_links = Link.search([
            ('task_id', '=', task.id),
            ('role', '=', role),
        ])
        if target_employee_id:
            keep = existing_links.filtered(lambda link: link.employee_id.id == target_employee_id)[:1]
            (existing_links - keep).unlink()
            if not keep:
                Link.create({
                    'task_id': task.id,
                    'employee_id': target_employee_id,
                    'role': role,
                })
        else:
            existing_links.unlink()

    def _compute_role_assignee_user_ids(self):
        self.ensure_one()
        user_ids = []
        if 'x_bitrix_responsible_employee_id' in self._fields and self.x_bitrix_responsible_employee_id:
            user = self._get_user_from_employee(self.x_bitrix_responsible_employee_id)
            if user and user.id not in user_ids:
                user_ids.append(user.id)
        if 'x_bitrix_accomplice_employee_ids' in self._fields:
            for employee in self.x_bitrix_accomplice_employee_ids:
                user = self._get_user_from_employee(employee)
                if user and user.id not in user_ids:
                    user_ids.append(user.id)
        return sorted(set(user_ids))

    def _compute_current_assignee_user_ids(self):
        self.ensure_one()
        if 'x_bitrix_assignee_user_ids' in self._fields and self.x_bitrix_assignee_user_ids:
            return sorted(set(self.x_bitrix_assignee_user_ids.ids))
        return self._compute_role_assignee_user_ids()

    def _compute_current_auditor_user_ids(self):
        self.ensure_one()
        if 'x_bitrix_auditor_employee_ids' not in self._fields:
            return []
        return sorted(set(self._get_users_from_employees(self.x_bitrix_auditor_employee_ids).ids))

    def _compute_current_originator_user_ids(self):
        self.ensure_one()
        if 'x_bitrix_originator_employee_id' not in self._fields:
            return []
        user = self._get_user_from_employee(self.x_bitrix_originator_employee_id)
        return [user.id] if user else []

    def _compute_current_creator_user_ids(self):
        self.ensure_one()
        if 'x_bitrix_creator_employee_id' not in self._fields:
            return []
        user = self._get_user_from_employee(self.x_bitrix_creator_employee_id)
        return [user.id] if user else []

    def _sync_bitrix_user_access(self, mirror_assignee_users=False):
        for task in self:
            assignee_ids = (
                task._compute_role_assignee_user_ids()
                if mirror_assignee_users
                else task._compute_current_assignee_user_ids()
            )

            vals = {}
            if 'user_ids' in task._fields and set(task.user_ids.ids) != set(assignee_ids):
                vals['user_ids'] = [(6, 0, assignee_ids)]

            if vals:
                task.with_context(bitrix_skip_user_sync=True).write(vals)

    def _sync_assignee_storage_from_user_ids(self):
        return

    def _sync_user_ids_from_assignee_storage(self):
        return

    def _ensure_responsible_user_in_assignees(self, explicit_user=None):
        for task in self:
            responsible_user = explicit_user or task.x_bitrix_responsible_user_id
            if not responsible_user:
                task._sync_bitrix_user_access(mirror_assignee_users=False)
                continue

            assignee_ids = sorted(set(
                task.x_bitrix_assignee_user_ids.ids
                or task.user_ids.ids
                or task._compute_role_assignee_user_ids()
            ))
            if responsible_user.id not in assignee_ids:
                assignee_ids.append(responsible_user.id)
                assignee_ids = sorted(set(assignee_ids))

            vals = {}
            if 'user_ids' in task._fields and set(task.user_ids.ids) != set(assignee_ids):
                vals['user_ids'] = [(6, 0, assignee_ids)]
            if vals:
                task.with_context(bitrix_skip_user_sync=True).write(vals)

    # ── Computes ─────────────────────────────────────────────────────

    def _compute_bitrix_responsible_employee_id(self):
        self._compute_bitrix_m2o_employee_id('responsible', 'x_bitrix_responsible_employee_id')

    def _compute_bitrix_responsible_employee_ids(self):
        """Deprecated readonly mirror: returns 0 or 1 employees from canonical responsible."""
        links = self.env['bitrix.task.employee.link'].sudo().search([
            ('task_id', 'in', self.ids),
            ('role', '=', 'responsible'),
        ])
        by_task = {}
        for link in links:
            by_task.setdefault(link.task_id.id, link.employee_id.id)
        for task in self:
            emp_id = by_task.get(task.id)
            task.x_bitrix_responsible_employee_ids = [(6, 0, [emp_id] if emp_id else [])]

    def _compute_bitrix_responsible_user_id(self):
        for task in self:
            task.x_bitrix_responsible_user_id = task._get_user_from_employee(
                task.x_bitrix_responsible_employee_id
            )

    def _compute_bitrix_accomplice_employee_ids(self):
        self._compute_bitrix_employee_ids('accomplice', 'x_bitrix_accomplice_employee_ids')

    def _compute_bitrix_auditor_employee_ids(self):
        self._compute_bitrix_employee_ids('auditor', 'x_bitrix_auditor_employee_ids')

    @api.depends('x_bitrix_auditor_employee_ids.user_id')
    def _compute_bitrix_auditor_user_ids(self):
        for task in self:
            task.x_bitrix_auditor_user_ids = [(6, 0, task._compute_current_auditor_user_ids())]

    @api.depends(
        'user_ids',
        'x_bitrix_auditor_employee_ids.user_id',
        'x_bitrix_originator_employee_id.user_id',
        'x_bitrix_creator_employee_id.user_id',
    )
    def _compute_bitrix_access_user_ids(self):
        for task in self:
            access_ids = sorted(set(
                task._compute_current_assignee_user_ids()
                + task._compute_current_auditor_user_ids()
                + task._compute_current_originator_user_ids()
                + task._compute_current_creator_user_ids()
            ))
            task.x_bitrix_access_user_ids = [(6, 0, access_ids)]

    def _compute_bitrix_originator_employee_id(self):
        self._compute_bitrix_m2o_employee_id('originator', 'x_bitrix_originator_employee_id')

    def _compute_bitrix_participant_employee_ids(self):
        self._compute_bitrix_employee_ids('participant', 'x_bitrix_participant_employee_ids')

    # ── Inverses ─────────────────────────────────────────────────────

    def _inverse_bitrix_employee_ids(self, role, field_name):
        Link = self.env['bitrix.task.employee.link'].sudo()
        for task in self:
            target_ids = set(task[field_name].ids)
            existing_links = Link.search([
                ('task_id', '=', task.id),
                ('role', '=', role),
            ])
            existing_ids = set(existing_links.mapped('employee_id').ids)
            to_remove = sorted(existing_ids - target_ids)
            to_add = sorted(target_ids - existing_ids)
            if to_remove:
                Link.search([
                    ('task_id', '=', task.id),
                    ('role', '=', role),
                    ('employee_id', 'in', to_remove),
                ]).unlink()
            if to_add:
                Link.create([
                    {
                        'task_id': task.id,
                        'employee_id': employee_id,
                        'role': role,
                    }
                    for employee_id in to_add
                ])

    def _inverse_bitrix_m2o_employee_id(self, role, field_name):
        Link = self.env['bitrix.task.employee.link'].sudo()
        for task in self:
            target_id = task[field_name].id if task[field_name] else False
            existing_links = Link.search([
                ('task_id', '=', task.id),
                ('role', '=', role),
            ])
            if target_id:
                existing_ids = set(existing_links.mapped('employee_id').ids)
                if target_id not in existing_ids:
                    existing_links.unlink()
                    Link.create({
                        'task_id': task.id,
                        'employee_id': target_id,
                        'role': role,
                    })
                elif len(existing_links) > 1:
                    to_keep = existing_links.filtered(lambda l: l.employee_id.id == target_id)[:1]
                    (existing_links - to_keep).unlink()
            else:
                existing_links.unlink()

    def _inverse_bitrix_responsible_employee_id(self):
        self._inverse_bitrix_m2o_employee_id('responsible', 'x_bitrix_responsible_employee_id')
        for task in self:
            task._ensure_responsible_user_in_assignees(
                explicit_user=task._get_user_from_employee(task.x_bitrix_responsible_employee_id)
            )

    def _inverse_bitrix_responsible_user_id(self):
        for task in self:
            explicit_user = task.x_bitrix_responsible_user_id
            employee = task._get_employee_from_user(explicit_user)
            if explicit_user:
                employees = task._get_employees_from_users(
                    explicit_user, 'Відповідальний',
                )
                employee = employees[:1]
            task._set_role_employee_id(task, 'responsible', employee.id if employee else False)
            task.invalidate_recordset(['x_bitrix_responsible_employee_id', 'x_bitrix_responsible_user_id'])
            task._ensure_responsible_user_in_assignees(explicit_user=explicit_user)

    def _inverse_bitrix_accomplice_employee_ids(self):
        self._inverse_bitrix_employee_ids('accomplice', 'x_bitrix_accomplice_employee_ids')
        self._sync_bitrix_user_access(mirror_assignee_users=True)

    def _inverse_bitrix_auditor_employee_ids(self):
        self._inverse_bitrix_employee_ids('auditor', 'x_bitrix_auditor_employee_ids')
        self._sync_bitrix_user_access(mirror_assignee_users=False)

    def _inverse_bitrix_auditor_user_ids(self):
        for task in self:
            employees = task._get_employees_from_users(task.x_bitrix_auditor_user_ids, 'Наглядачі')
            task._set_role_employee_ids(task, 'auditor', employees.ids)
        self.invalidate_recordset(['x_bitrix_auditor_employee_ids', 'x_bitrix_auditor_user_ids'])
        self._sync_bitrix_user_access(mirror_assignee_users=False)

    def _inverse_bitrix_originator_employee_id(self):
        self._inverse_bitrix_m2o_employee_id('originator', 'x_bitrix_originator_employee_id')

    def _inverse_bitrix_participant_employee_ids(self):
        self._inverse_bitrix_employee_ids('participant', 'x_bitrix_participant_employee_ids')

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for vals in vals_list:
            normalized_vals = dict(vals)
            if (
                normalized_vals.get('x_bitrix_id')
                and 'user_ids' not in normalized_vals
                and 'x_bitrix_assignee_user_ids' not in normalized_vals
            ):
                normalized_vals['user_ids'] = [(6, 0, [])]
            normalized_vals_list.append(normalized_vals)

        tasks = super().create(normalized_vals_list)
        if self.env.context.get('bitrix_skip_user_sync'):
            return tasks

        for task, vals in zip(tasks, normalized_vals_list):
            updated_fields = set(vals)
            if 'user_ids' in updated_fields:
                task._sync_assignee_storage_from_user_ids()
            if 'x_bitrix_assignee_user_ids' in updated_fields:
                task._sync_user_ids_from_assignee_storage()
            if updated_fields & {
                'x_bitrix_responsible_user_id',
                'x_bitrix_auditor_user_ids',
                'x_bitrix_responsible_employee_id',
                'x_bitrix_accomplice_employee_ids',
                'x_bitrix_auditor_employee_ids',
            }:
                task._sync_bitrix_user_access(
                    mirror_assignee_users=bool(
                        updated_fields & {
                            'x_bitrix_responsible_employee_id',
                            'x_bitrix_accomplice_employee_ids',
                        }
                    )
                )
        return tasks

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('bitrix_skip_user_sync'):
            return res

        updated_fields = set(vals)
        if 'user_ids' in updated_fields:
            self._sync_assignee_storage_from_user_ids()
        if 'x_bitrix_assignee_user_ids' in updated_fields:
            self._sync_user_ids_from_assignee_storage()
        if updated_fields & {
            'x_bitrix_responsible_user_id',
            'x_bitrix_auditor_user_ids',
            'x_bitrix_responsible_employee_id',
            'x_bitrix_accomplice_employee_ids',
            'x_bitrix_auditor_employee_ids',
        }:
            self._sync_bitrix_user_access(
                mirror_assignee_users=bool(
                    updated_fields & {
                        'x_bitrix_responsible_employee_id',
                        'x_bitrix_accomplice_employee_ids',
                    }
                )
            )
        return res

    # ── Search helpers ───────────────────────────────────────────────

    def _normalize_employee_search_value(self, value):
        if hasattr(value, 'ids'):
            return [int(v) for v in value.ids if v]
        if isinstance(value, (list, tuple, set)):
            return [int(v) for v in value if v]
        if value in (False, None):
            return []
        if isinstance(value, (str, bytes)):
            return [int(value)] if value else []
        if hasattr(value, '__iter__'):
            return [int(v) for v in list(value) if v]
        if value:
            return [int(value)]
        return []

    def _search_bitrix_employee_ids(self, role, operator, value):
        Link = self.env['bitrix.task.employee.link'].sudo()
        if operator in ('any', 'not any'):
            # Support nested domains like `x_bitrix_auditor_employee_ids.user_id = uid`
            # used by record rules and action domains.
            employee_domain = value or []
            employee_ids = self.env['hr.employee'].sudo().search(employee_domain).ids
            if not employee_ids:
                return [('id', '=', 0)] if operator == 'any' else []

            task_ids = Link.search([
                ('role', '=', role),
                ('employee_id', 'in', employee_ids),
            ]).mapped('task_id').ids
            if operator == 'any':
                return [('id', 'in', task_ids or [0])]
            return [('id', 'not in', task_ids or [0])]

        if operator not in ('in', 'not in', '=', '!='):
            raise NotImplementedError(
                'Operator "%s" is not supported for bitrix employee link fields.' % operator
            )

        employee_ids = self._normalize_employee_search_value(value)
        if operator in ('=', '!='):
            if not employee_ids:
                task_ids_with_role = Link.search([('role', '=', role)]).mapped('task_id').ids
                if operator == '=':
                    return [('id', 'not in', task_ids_with_role or [0])]
                return [('id', 'in', task_ids_with_role or [0])]
            operator = 'in' if operator == '=' else 'not in'

        if not employee_ids:
            return [] if operator == 'not in' else [('id', '=', 0)]

        task_ids = Link.search([
            ('role', '=', role),
            ('employee_id', 'in', employee_ids),
        ]).mapped('task_id').ids

        if operator == 'in':
            return [('id', 'in', task_ids or [0])]
        return [('id', 'not in', task_ids or [0])]

    def _search_bitrix_responsible_employee_id(self, operator, value):
        return self._search_bitrix_employee_ids('responsible', operator, value)

    def _search_bitrix_responsible_employee_ids(self, operator, value):
        return self._search_bitrix_employee_ids('responsible', operator, value)

    def _search_bitrix_accomplice_employee_ids(self, operator, value):
        return self._search_bitrix_employee_ids('accomplice', operator, value)

    def _search_bitrix_auditor_employee_ids(self, operator, value):
        return self._search_bitrix_employee_ids('auditor', operator, value)

    def _search_bitrix_originator_employee_id(self, operator, value):
        return self._search_bitrix_employee_ids('originator', operator, value)

    def _search_bitrix_participant_employee_ids(self, operator, value):
        return self._search_bitrix_employee_ids('participant', operator, value)
