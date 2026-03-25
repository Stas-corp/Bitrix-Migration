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
