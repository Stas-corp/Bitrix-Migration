# -*- coding: utf-8 -*-
{
    'name': 'DMS Access Fix',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': (
        'Read-only sharing for Document User; closes the base.group_user '
        'ACL leak in odoo_document_management_cloud_sync'
    ),
    'author': 'Bitrix-Migration project',
    'license': 'LGPL-3',
    'depends': [
        'odoo_document_management_cloud_sync',
    ],
    'data': [
        'security/document_access_fix.xml',
        'views/document_folder_views.xml',
    ],
    'post_init_hook': 'post_init',
    'installable': True,
    'auto_install': True,
    'application': False,
}
