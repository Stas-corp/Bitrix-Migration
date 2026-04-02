from odoo import models, fields


class ProjectTaskType(models.Model):
    _inherit = 'project.task.type'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
    x_bitrix_entity_type = fields.Selection([
        ('G', 'Group'),
        ('U', 'User'),
    ], string='Bitrix Entity Type', copy=False)
    x_bitrix_entity_id = fields.Char(string='Bitrix Entity ID', copy=False)
