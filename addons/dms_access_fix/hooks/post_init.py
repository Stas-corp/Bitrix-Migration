# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def post_init(env):
    """Clean up legacy ACL/ir.rule left over from the paid module's old data
    files and post_init_hook.

    The paid module v19.0.0.1 ships a `data/access_rules.xml` and a
    `post_init_hook` that create ACLs granting full CRUD on every document.*
    model to `base.group_user` (every Internal User). Those ACLs are
    `noupdate="1"` — they survive upgrades. Plus, the original `ir.rule`
    entries `document_folder_user_rule`, `document_file_user_rule` and their
    Model-User counterparts have `perm_read=perm_write=perm_create=perm_unlink=True`,
    so they OR with our two-tier rules and restore broad write/unlink.

    This hook removes both, on top of the XML overrides in
    `security/document_access_fix.xml`.

    Idempotent — safe to re-run (uninstall + install) any time.
    """
    cr = env.cr

    # 1) drop every ACL on document.* models pointing at base.group_user
    cr.execute("""
        DELETE FROM ir_model_access
        WHERE group_id = (
            SELECT res_id FROM ir_model_data
            WHERE module = 'base' AND name = 'group_user'
        )
        AND model_id IN (
            SELECT id FROM ir_model WHERE model LIKE 'document.%%'
        )
    """)
    _logger.info("dms_access_fix: removed %s base.group_user ACL(s) on document.*", cr.rowcount)

    # 2) drop legacy broad ir.rule entries from the paid module
    stale_rule_xmlids = (
        'document_folder_user_rule',
        'document_file_user_rule',
        'document_folder_model_user_rule',
        'document_file_model_user_rule',
    )
    cr.execute("""
        DELETE FROM ir_rule
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'odoo_document_management_cloud_sync'
              AND model = 'ir.rule'
              AND name IN %s
        )
    """, (stale_rule_xmlids,))
    _logger.info("dms_access_fix: removed %s legacy broad ir.rule(s)", cr.rowcount)

    # 3) clean orphaned ir_model_data rows for the deletes above
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE model = 'ir.model.access'
          AND res_id NOT IN (SELECT id FROM ir_model_access)
    """)
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE model = 'ir.rule'
          AND res_id NOT IN (SELECT id FROM ir_rule)
    """)
