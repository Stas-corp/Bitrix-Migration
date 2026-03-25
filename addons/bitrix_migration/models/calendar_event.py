from odoo import models, fields


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    x_bitrix_id = fields.Char(string='Bitrix ID', index=True, copy=False)
