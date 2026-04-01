from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
    x_bitrix_stage_id = fields.Char(string='Bitrix Stage ID', copy=False)
    x_bitrix_parent_id = fields.Char(string='Bitrix Parent ID', copy=False)
    x_bitrix_created_at = fields.Datetime(string='Bitrix Created At', copy=False)
    x_bitrix_responsible_employee_ids = fields.Many2many(
        'hr.employee',
        'project_task_bitrix_employee_rel',
        'task_id',
        'employee_id',
        string='Bitrix Responsible Employees',
        copy=False,
        help='Employees resolved from Bitrix responsible users, preserved even before linked Odoo users exist.',
    )
    x_bitrix_participant_employee_ids = fields.Many2many(
        'hr.employee',
        'project_task_bitrix_participant_rel',
        'task_id',
        'employee_id',
        string='Bitrix Participants',
        copy=False,
        help='All Bitrix task participants included in Odoo assignees: responsible users, accomplices, auditors, and creator.',
    )
    x_bitrix_creator_employee_id = fields.Many2one(
        'hr.employee',
        string='Bitrix Creator (Employee)',
        copy=False,
        help='Employee who created this task in Bitrix.',
    )
