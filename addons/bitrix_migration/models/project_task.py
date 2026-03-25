from odoo import models, fields


class ProjectTask(models.Model):
    _inherit = 'project.task'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
    x_bitrix_stage_id = fields.Char(string='Bitrix Stage ID', copy=False)
    x_bitrix_parent_id = fields.Char(string='Bitrix Parent ID', copy=False)
