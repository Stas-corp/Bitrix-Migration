# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import AccessError


ACL_FIELDS = ('user_ids', 'group_ids')

MANAGER_GROUP = 'odoo_document_management_cloud_sync.group_document_manager'


class DocumentFolder(models.Model):
    _inherit = 'document.folder'

    can_manage_access = fields.Boolean(
        compute='_compute_can_manage_access',
    )

    @api.depends('user_id')
    @api.depends_context('uid')
    def _compute_can_manage_access(self):
        is_manager = (
            self.env.user.has_group(MANAGER_GROUP)
            or self.env.is_superuser()
        )
        for folder in self:
            folder.can_manage_access = is_manager or folder.user_id.id == self.env.user.id

    def _compute_is_favorite(self):
        """Override to use cache writes — original assigns to non-stored field
        via ``__set__``, which triggers ``write()`` → AccessError for users with
        only read access on someone else's folder."""
        if not self.ids:
            return
        favs = self.env['document.folder.favorite'].sudo().search([
            ('user_id', '=', self.env.user.id),
            ('folder_id', 'in', self.ids),
        ])
        favorite_ids = set(favs.mapped('folder_id.id'))
        cache = self.env.cache
        field = self._fields['is_favorite']
        for folder in self:
            cache.set(folder, field, folder.id in favorite_ids)

    def write(self, vals):
        if isinstance(vals, dict) and any(f in vals for f in ACL_FIELDS):
            is_manager = (
                self.env.user.has_group(MANAGER_GROUP)
                or self.env.is_superuser()
            )
            if not is_manager:
                for rec in self:
                    if rec.user_id.id != self.env.user.id:
                        raise AccessError(_(
                            "Only the folder owner or a Document Manager can "
                            "change folder access (user_ids / group_ids)."
                        ))
        return super().write(vals)
