from odoo import fields, models


class HrDepartmentBitrix(models.Model):
    _inherit = 'hr.department'

    x_bitrix_id = fields.Integer(string='Bitrix ID', index=True, default=0)
