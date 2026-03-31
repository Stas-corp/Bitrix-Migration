from odoo import models, fields


class MailMessage(models.Model):
    _inherit = 'mail.message'

    x_bitrix_message_id = fields.Char(
        string='Bitrix Forum Message ID',
        index=True,
        copy=False,
        help='Original Bitrix forum message ID for idempotent comment migration.',
    )
    x_bitrix_author_id = fields.Char(
        string='Bitrix Author ID',
        copy=False,
        help='Original Bitrix author ID, stored when fallback system author was used.',
    )
    x_bitrix_author_employee_id = fields.Many2one(
        'hr.employee',
        string='Bitrix Author Employee',
        copy=False,
        help='Employee matched to the Bitrix author, even when the message uses a fallback partner.',
    )
