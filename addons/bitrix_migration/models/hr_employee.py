from odoo import fields, models


class HrEmployeeBitrix(models.Model):
    _inherit = 'hr.employee'

    x_bitrix_id = fields.Integer(string='Bitrix ID', index=True, default=0, groups='hr.group_hr_user')
    x_bitrix_telegram = fields.Char(string='Telegram (Bitrix)', groups='hr.group_hr_user')
