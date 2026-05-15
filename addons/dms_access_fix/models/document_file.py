# -*- coding: utf-8 -*-
from odoo import models


class DocumentFile(models.Model):
    """Override non-stored computed fields that the paid module writes via
    ``record.field = value``.

    Those assignments go through ``__set__`` → ``write()`` → ``check_access('write')``,
    which raises ``AccessError`` for users that only have read access to the
    file. ``get_files()`` (called by the JS dashboard) explicitly calls these
    computes, so the entire dashboard payload crashes for share recipients and
    the file list comes back empty.

    Writing the value into ``env.cache`` directly bypasses ``__set__`` /
    ``write`` entirely — the values still end up on the recordset, but there's
    no ACL/rule check, which is the right thing for non-stored computes.
    """
    _inherit = 'document.file'

    def _compute_is_locked(self):
        Lock = self.env['document.file.lock'].sudo()
        cache = self.env.cache
        field = self._fields['is_locked']
        for file in self:
            active = Lock.search([
                ('file_id', '=', file.id),
                ('is_active', '=', True),
            ], limit=1)
            cache.set(file, field, bool(active))

    def _compute_locked_info(self):
        Lock = self.env['document.file.lock'].sudo()
        cache = self.env.cache
        field = self._fields['locked_by_id']
        for file in self:
            active = Lock.search([
                ('file_id', '=', file.id),
                ('is_active', '=', True),
            ], limit=1)
            cache.set(file, field, active.locked_by.id if active else False)

    def _compute_is_favorite(self):
        if not self.ids:
            return
        favs = self.env['document.file.favorite'].sudo().search([
            ('user_id', '=', self.env.user.id),
            ('file_id', 'in', self.ids),
        ])
        favorite_ids = set(favs.mapped('file_id.id'))
        cache = self.env.cache
        field = self._fields['is_favorite']
        for file in self:
            cache.set(file, field, file.id in favorite_ids)
