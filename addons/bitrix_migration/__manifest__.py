{
    'name': 'Bitrix24 Migration',
    'version': '19.0.1.0.0',
    'category': 'Tools',
    'summary': 'Migrate data from Bitrix24 to Odoo 19 CE',
    'description': 'Migration module for Bitrix24 → Odoo 19 CE: projects, tasks, stages, tags, comments, attachments.',
    'depends': ['project', 'mail', 'calendar', 'hr', 'auth_signup'],
    'data': [
        'data/ir.model.access.csv',
        'views/bitrix_migration_run_views.xml',
        'views/hr_employee_views.xml',
        'views/project_task_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'external_dependencies': {
        'python': ['pymysql', 'pydantic', 'paramiko'],
    },
}
