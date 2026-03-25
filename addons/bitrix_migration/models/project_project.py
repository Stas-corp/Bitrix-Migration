from odoo import models, fields


class ProjectProject(models.Model):
    _inherit = 'project.project'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
    x_bitrix_type = fields.Selection([
        ('project', 'Project'),
        ('workgroup', 'Workgroup'),
    ], string='Bitrix Type', copy=False)
    x_bitrix_closed = fields.Boolean(string='Bitrix Closed', default=False, copy=False)
    x_bitrix_owner_bitrix_id = fields.Char(string='Bitrix Owner ID', copy=False)
