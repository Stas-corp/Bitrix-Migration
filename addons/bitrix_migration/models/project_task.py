from collections import defaultdict

from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
    x_bitrix_stage_id = fields.Char(string='Bitrix Stage ID', copy=False)
    x_bitrix_parent_id = fields.Char(string='Bitrix Parent ID', copy=False)
    x_bitrix_created_at = fields.Datetime(string='Bitrix Created At', copy=False)
    x_bitrix_responsible_employee_ids = fields.Many2many(
        'hr.employee',
        string='Bitrix Responsible Employees',
        compute='_compute_bitrix_responsible_employee_ids',
        inverse='_inverse_bitrix_responsible_employee_ids',
        search='_search_bitrix_responsible_employee_ids',
        copy=False,
        help='Employees resolved from Bitrix responsible users, preserved even before linked Odoo users exist.',
    )
    x_bitrix_participant_employee_ids = fields.Many2many(
        'hr.employee',
        string='Bitrix Participants',
        compute='_compute_bitrix_participant_employee_ids',
        inverse='_inverse_bitrix_participant_employee_ids',
        search='_search_bitrix_participant_employee_ids',
        copy=False,
        help='All Bitrix task participants included in Odoo assignees: responsible users, accomplices, auditors, and creator.',
    )
    x_bitrix_creator_employee_id = fields.Many2one(
        'hr.employee',
        string='Bitrix Creator (Employee)',
        copy=False,
        help='Employee who created this task in Bitrix.',
    )

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

    def _compute_bitrix_responsible_employee_ids(self):
        self._compute_bitrix_employee_ids('responsible', 'x_bitrix_responsible_employee_ids')

    def _compute_bitrix_participant_employee_ids(self):
        self._compute_bitrix_employee_ids('participant', 'x_bitrix_participant_employee_ids')

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

    def _inverse_bitrix_responsible_employee_ids(self):
        self._inverse_bitrix_employee_ids('responsible', 'x_bitrix_responsible_employee_ids')

    def _inverse_bitrix_participant_employee_ids(self):
        self._inverse_bitrix_employee_ids('participant', 'x_bitrix_participant_employee_ids')

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

    def _search_bitrix_responsible_employee_ids(self, operator, value):
        return self._search_bitrix_employee_ids('responsible', operator, value)

    def _search_bitrix_participant_employee_ids(self, operator, value):
        return self._search_bitrix_employee_ids('participant', operator, value)
