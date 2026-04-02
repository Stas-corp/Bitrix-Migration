import logging
import re
import traceback

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class BitrixMigrationRun(models.Model):
    _name = 'bitrix.migration.run'
    _description = 'Bitrix Migration Runner'

    mode = fields.Selection([
        ('full', 'Full Migration'),
        ('dry_run', 'Dry Run'),
        ('pilot', 'Pilot (3-5 projects)'),
        ('projects_only', 'Projects Only'),
        ('relink', 'Relink Parents'),
        ('comments', 'Comments Only'),
        ('single_task', 'Single Task Test'),
        ('hr', 'HR: Departments + Employees'),
        ('departments_only', 'HR: Departments Only'),
        ('employees_only', 'HR: Employees Only'),
    ], required=True, default='dry_run', string='Mode')

    # MySQL connection
    mysql_host = fields.Char(string='MySQL Host', default='localhost')
    mysql_port = fields.Integer(string='MySQL Port', default=3306)
    mysql_user = fields.Char(string='MySQL User')
    mysql_password = fields.Char(string='MySQL Password')
    mysql_database = fields.Char(string='MySQL Database')

    # SFTP
    sftp_host = fields.Char(string='SFTP Host')
    sftp_port = fields.Integer(string='SFTP Port', default=22)
    sftp_user = fields.Char(string='SFTP User')
    sftp_key_path = fields.Char(string='SFTP Key Path')
    sftp_base_path = fields.Char(string='SFTP Base Path', default='/home/bitrix/www')

    # Options
    preserve_authorship = fields.Boolean(string='Preserve Authorship', default=True)
    fallback_system_author = fields.Boolean(string='Fallback System Author', default=True)
    migration_date_from = fields.Date(string='Import From Date')

    # Mode-specific
    pilot_project_ids = fields.Char(string='Pilot Project IDs (comma-separated)')
    single_task_bitrix_id = fields.Char(string='Single Task Bitrix ID')
    fallback_project_id = fields.Many2one(
        'project.project',
        string='Fallback Project (no-project tasks)',
        help='Tasks without a Bitrix project will be assigned here. '
             'Auto-created as "Bitrix: Без проекта" if left empty.',
    )
    test_employee_id = fields.Many2one(
        'hr.employee',
        string='Test Employee',
        domain=[('x_bitrix_id', '!=', 0)],
    )

    # State
    log_output = fields.Text(string='Log Output', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft', string='State')
    progress = fields.Float(string='Progress', default=0.0)

    def _append_log(self, message):
        self.ensure_one()
        try:
            current_log = self.sudo().read(['log_output'])[0].get('log_output') or ''
        except Exception:
            self.env.cr.rollback()
            current_log = self.sudo().read(['log_output'])[0].get('log_output') or ''

        new_log = f'{current_log}\n{message}' if current_log else message
        self.sudo().write({'log_output': new_log})
        self.env.cr.commit()

    def _get_extractor(self):
        from ..services.extractors.bitrix_mysql import BitrixMySQLExtractor
        return BitrixMySQLExtractor(
            host=self.mysql_host,
            port=self.mysql_port,
            user=self.mysql_user,
            password=self.mysql_password,
            database=self.mysql_database,
            date_from=self.migration_date_from,
        )

    def action_run(self):
        self.ensure_one()
        self.state = 'running'
        self.log_output = f'=== Migration started: mode={self.mode} ==='
        self.progress = 0.0
        self.env.cr.commit()
        if self.migration_date_from:
            self._append_log(
                f'=== Active date filter: import from {self.migration_date_from} ==='
            )

        extractor = None
        try:
            extractor = self._get_extractor()
            dry_run = self.mode == 'dry_run'

            if self.mode == 'dry_run':
                self._run_dry_run(extractor)
            elif self.mode == 'single_task':
                self._run_single_task(extractor)
            elif self.mode == 'full':
                self._run_full(extractor, dry_run=False)
            elif self.mode == 'pilot':
                self._run_pilot(extractor)
            elif self.mode == 'projects_only':
                self._run_projects_only(extractor)
            elif self.mode == 'relink':
                self._run_relink(extractor)
            elif self.mode == 'comments':
                self._run_comments(extractor)
            elif self.mode == 'hr':
                self._run_hr(extractor)
            elif self.mode == 'departments_only':
                self._run_departments_only(extractor)
            elif self.mode == 'employees_only':
                self._run_employees_only(extractor)

            self.state = 'done'
            self.progress = 100.0
            self._append_log('=== Migration completed successfully ===')

        except Exception:
            self.env.cr.rollback()
            self.state = 'error'
            self._append_log(f'=== ERROR ===\n{traceback.format_exc()}')
            _logger.exception('Migration failed')
        finally:
            if extractor:
                try:
                    extractor.close()
                except Exception:
                    _logger.warning('Could not close Bitrix extractor cleanly', exc_info=True)

        self.env.cr.commit()

    def _run_dry_run(self, extractor):
        """Count all entities without writing to Odoo."""
        self._append_log('--- DRY RUN: counting entities ---')
        counts = {
            'Projects': extractor.count_projects(),
            'Tasks': extractor.count_tasks(),
            'Tags': extractor.count_tags(),
            'Stages (G+U)': extractor.count_stages(),
            'Comments (real)': extractor.count_comments(),
            'Meetings': extractor.count_meetings(),
            'Departments': extractor.count_departments(),
            'Employees': extractor.count_employees(),
        }

        # Odoo existing counts
        odoo_counts = {
            'Projects': self.env['project.project'].sudo().search_count(
                [('x_bitrix_id', '!=', False)]),
            'Tasks': self.env['project.task'].sudo().with_context(active_test=False).search_count(
                [('x_bitrix_id', '!=', False)]),
            'Comments': self.env['mail.message'].sudo().search_count(
                [('x_bitrix_message_id', '!=', False)]),
            'Departments': self.env['hr.department'].sudo().search_count(
                [('x_bitrix_id', '!=', 0)]),
            'Employees': self.env['hr.employee'].sudo().with_context(active_test=False).search_count(
                [('x_bitrix_id', '!=', 0)]),
        }

        self._append_log(f"{'Entity':<20} {'Bitrix':>10} {'Odoo':>10} {'To Create':>10}")
        self._append_log('-' * 55)
        for entity, cnt in counts.items():
            odoo_cnt = odoo_counts.get(entity, 0)
            self._append_log(f'{entity:<20} {cnt:>10} {odoo_cnt:>10} {cnt - odoo_cnt:>10}')

    def _ensure_fallback_project(self):
        """Return the fallback project id, creating it if needed."""
        self.ensure_one()
        if self.fallback_project_id:
            return self.fallback_project_id.id

        Project = self.env['project.project'].sudo()
        name = 'Bitrix: Без проекта'
        project = Project.search([('name', '=', name)], limit=1)
        if not project:
            project = Project.with_context(
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
                tracking_disable=True,
            ).create({
                'name': name,
                'privacy_visibility': 'followers',
                'active': True,
            })
            self._append_log(f'Auto-created fallback project: "{name}" (id={project.id})')
        self.fallback_project_id = project
        self.env.cr.commit()
        return project.id

    def _run_full(self, extractor, dry_run=False):
        """Full migration in correct order."""
        from ..services.loaders.users import UserLoader
        from ..services.loaders.tags import TagLoader
        from ..services.loaders.projects import ProjectLoader
        from ..services.loaders.stages import StageLoader
        from ..services.loaders.tasks import TaskLoader
        from ..services.loaders.tasks_relink import TaskRelinkLoader
        from ..services.loaders.comments import CommentLoader
        from ..services.loaders.attachments import AttachmentLoader

        steps = [
            ('Users', UserLoader, {}),
            ('Tags', TagLoader, {}),
            ('Projects', ProjectLoader, {}),
            ('Stages', StageLoader, {}),
            ('Tasks', TaskLoader, {'fallback_project_id': self._ensure_fallback_project()}),
            ('Task Relink', TaskRelinkLoader, {}),
            ('Comments', CommentLoader, {
                'preserve_authorship': self.preserve_authorship,
                'fallback_system_author': self.fallback_system_author,
            }),
        ]

        # Add attachment steps only if SFTP is configured
        if self.sftp_host:
            sftp_kwargs = {
                'sftp_host': self.sftp_host,
                'sftp_port': self.sftp_port,
                'sftp_user': self.sftp_user,
                'sftp_key_path': self.sftp_key_path,
                'sftp_base_path': self.sftp_base_path or '/home/bitrix/www',
            }
            steps.append(('Task Attachments', AttachmentLoader, sftp_kwargs))

        total_steps = len(steps)
        for i, (name, LoaderClass, kwargs) in enumerate(steps):
            self._append_log(f'\n--- Step {i+1}/{total_steps}: {name} ---')
            self.progress = (i / total_steps) * 100
            self.env.cr.commit()

            loader = LoaderClass(
                env=self.env,
                extractor=extractor,
                dry_run=dry_run,
                log_callback=self._append_log,
                **kwargs,
            )
            loader.run()

        # Run comment attachments separately if SFTP configured
        if self.sftp_host:
            self._append_log(f'\n--- Step: Comment Attachments ---')
            att_loader = AttachmentLoader(
                env=self.env,
                extractor=extractor,
                dry_run=dry_run,
                log_callback=self._append_log,
                sftp_host=self.sftp_host,
                sftp_port=self.sftp_port,
                sftp_user=self.sftp_user,
                sftp_key_path=self.sftp_key_path,
                sftp_base_path=self.sftp_base_path or '/home/bitrix/www',
            )
            att_loader.run(attachment_type='comment')

        self._run_reconciliation()

    def _run_single_task(self, extractor):
        """Load a single task with all its comments and attachments."""
        from ..services.loaders.users import UserLoader
        from ..services.loaders.tags import TagLoader
        from ..services.loaders.projects import ProjectLoader
        from ..services.loaders.stages import StageLoader
        from ..services.loaders.tasks import TaskLoader
        from ..services.loaders.comments import CommentLoader
        from ..services.loaders.attachments import AttachmentLoader

        task_id = self.single_task_bitrix_id
        if not task_id:
            self._append_log('ERROR: single_task_bitrix_id is required')
            return

        self._append_log(f'Loading single task: bitrix_id={task_id}')

        # 1. Extract the single task
        raw_tasks = extractor.get_single_task(int(task_id))
        if not raw_tasks:
            self._append_log(f'ERROR: Task {task_id} not found in Bitrix')
            return

        task_row = raw_tasks[0]
        self._append_log(f'Task: {task_row.get("name")} (project={task_row.get("project_external_id")})')

        # 2. Users mapping
        self._append_log('\n--- Users ---')
        user_loader = UserLoader(self.env, extractor, log_callback=self._append_log)
        user_loader.run()

        # 3. Tags for this task
        self._append_log('\n--- Tags ---')
        tag_loader = TagLoader(self.env, extractor, log_callback=self._append_log)
        tag_loader.run()

        # 4. Project for this task
        project_id = task_row.get('project_external_id')
        if project_id:
            self._append_log(f'\n--- Project {project_id} ---')
            raw_projects = [p for p in extractor.get_projects() if p['external_id'] == project_id]
            if raw_projects:
                proj_loader = ProjectLoader(self.env, extractor, log_callback=self._append_log)
                for row in raw_projects:
                    from ..normalizers.dto import BitrixProject
                    proj = BitrixProject(**row)
                    vals = {
                        'name': proj.name,
                        'description': proj.description or '',
                        'x_bitrix_id': str(proj.external_id),
                        'x_bitrix_type': proj.type,
                        'x_bitrix_closed': proj.closed,
                        'x_bitrix_owner_bitrix_id': str(proj.owner_bitrix_id) if proj.owner_bitrix_id else '',
                        'active': True,
                    }
                    proj_loader.get_or_create(
                        'project.project',
                        [('x_bitrix_id', '=', str(proj.external_id))],
                        vals,
                        bitrix_id=proj.external_id,
                        entity_type='project',
                    )
                proj_loader.commit_checkpoint(1)
                proj_loader.log_stats()

        # 5. Stage for this task
        stage_id = task_row.get('stage_id')
        if stage_id:
            self._append_log(f'\n--- Stage {stage_id} ---')
            raw_stages = [
                s for s in extractor.get_stages()
                if s.get('id', s.get('ID')) == stage_id
            ]
            if raw_stages:
                stage_loader = StageLoader(self.env, extractor, log_callback=self._append_log)
                stage_loader.run(raw_stages=raw_stages)
                stage_loader.commit_checkpoint(1)

        # 6. Task itself
        self._append_log(f'\n--- Task ---')
        task_loader = TaskLoader(
            self.env, extractor,
            log_callback=self._append_log,
            fallback_project_id=self._ensure_fallback_project(),
        )
        task_loader.run(raw_tasks=raw_tasks)

        # 7. Comments
        self._append_log(f'\n--- Comments ---')
        raw_comments = extractor.get_comments_for_task(int(task_id))
        self._append_log(f'Found {len(raw_comments)} comments for task {task_id}')
        comment_loader = CommentLoader(
            self.env, extractor,
            log_callback=self._append_log,
            preserve_authorship=self.preserve_authorship,
            fallback_system_author=self.fallback_system_author,
        )
        comment_loader.run(raw_comments=raw_comments)

        # 8. Attachments (if SFTP configured)
        if self.sftp_host:
            self._append_log(f'\n--- Task Attachments ---')
            raw_task_atts = extractor.get_task_attachments_for_task(int(task_id))
            att_loader = AttachmentLoader(
                self.env, extractor,
                log_callback=self._append_log,
                sftp_host=self.sftp_host,
                sftp_port=self.sftp_port,
                sftp_user=self.sftp_user,
                sftp_key_path=self.sftp_key_path,
                sftp_base_path=self.sftp_base_path or '/home/bitrix/www',
            )
            att_loader.run(attachment_type='task', raw_attachments=raw_task_atts)

            self._append_log(f'\n--- Comment Attachments ---')
            raw_comment_atts = extractor.get_comment_attachments_for_task(int(task_id))
            att_loader2 = AttachmentLoader(
                self.env, extractor,
                log_callback=self._append_log,
                sftp_host=self.sftp_host,
                sftp_port=self.sftp_port,
                sftp_user=self.sftp_user,
                sftp_key_path=self.sftp_key_path,
                sftp_base_path=self.sftp_base_path or '/home/bitrix/www',
            )
            att_loader2.run(attachment_type='comment', raw_attachments=raw_comment_atts)

        self._append_log('\n--- Single task migration complete ---')

    def _run_pilot(self, extractor):
        """Run migration for a few pilot projects."""
        self._append_log('Pilot mode: running full migration (will filter at SQL level if needed)')
        self._run_full(extractor, dry_run=False)

    def _run_projects_only(self, extractor):
        """Run only users, tags, projects, stages."""
        from ..services.loaders.users import UserLoader
        from ..services.loaders.tags import TagLoader
        from ..services.loaders.projects import ProjectLoader
        from ..services.loaders.stages import StageLoader

        for name, LoaderClass in [
            ('Users', UserLoader),
            ('Tags', TagLoader),
            ('Projects', ProjectLoader),
            ('Stages', StageLoader),
        ]:
            self._append_log(f'\n--- {name} ---')
            loader = LoaderClass(self.env, extractor, log_callback=self._append_log)
            loader.run()

    def _run_relink(self, extractor):
        """Run only task parent relinking."""
        from ..services.loaders.tasks_relink import TaskRelinkLoader
        self._append_log('\n--- Task Relink ---')
        loader = TaskRelinkLoader(self.env, extractor, log_callback=self._append_log)
        loader.run()

    def _run_comments(self, extractor):
        """Run only comment loading."""
        from ..services.loaders.comments import CommentLoader
        self._append_log('\n--- Comments ---')
        loader = CommentLoader(
            self.env, extractor,
            log_callback=self._append_log,
            preserve_authorship=self.preserve_authorship,
            fallback_system_author=self.fallback_system_author,
        )
        loader.run()

    def _run_hr(self, extractor):
        """Migrate departments (with hierarchy) then employees."""
        self._run_departments_only(extractor)
        self._run_employees_only(extractor)

    def _run_departments_only(self, extractor):
        """Migrate department structure from Bitrix into hr.department."""
        from ..services.loaders.users import UserLoader
        from ..services.loaders.departments import DepartmentLoader

        dry_run = self.mode == 'dry_run'

        self._append_log('\n--- Users (mapping) ---')
        user_loader = UserLoader(
            self.env, extractor,
            dry_run=dry_run,
            log_callback=self._append_log,
        )
        user_map = user_loader.run()

        self._append_log('\n--- Departments ---')
        dept_loader = DepartmentLoader(
            self.env, extractor,
            user_map=user_map or {},
            dry_run=dry_run,
            log_callback=self._append_log,
        )
        dept_loader.run()

    def _run_employees_only(self, extractor):
        """Migrate active employees into hr.employee (departments must exist)."""
        from ..services.loaders.users import UserLoader
        from ..services.loaders.employees import EmployeeLoader

        dry_run = self.mode == 'dry_run'

        # Build user map (may already be populated; run is idempotent)
        self._append_log('\n--- Users (mapping) ---')
        user_loader = UserLoader(
            self.env, extractor,
            dry_run=dry_run,
            log_callback=self._append_log,
        )
        user_map = user_loader.run()

        dept_map = self.env['bitrix.migration.mapping'].sudo().get_all_mappings('department')

        self._append_log('\n--- Employees ---')
        emp_loader = EmployeeLoader(
            self.env, extractor,
            user_map=user_map or {},
            dept_map=dept_map,
            dry_run=dry_run,
            log_callback=self._append_log,
        )
        emp_loader.run()

    def _run_reconciliation(self):
        """Print reconciliation report."""
        self._append_log('\n=== RECONCILIATION REPORT ===')
        checks = {
            'Projects': self.env['project.project'].sudo().search_count(
                [('x_bitrix_id', '!=', False)]),
            'Tasks': self.env['project.task'].sudo().with_context(active_test=False).search_count(
                [('x_bitrix_id', '!=', False)]),
            'Tasks without project': self.env['project.task'].sudo().with_context(active_test=False).search_count(
                [('x_bitrix_id', '!=', False), ('project_id', '=', False)]),
            'Stages': self.env['project.task.type'].sudo().search_count(
                [('x_bitrix_id', '!=', False)]),
            'Comments': self.env['mail.message'].sudo().search_count(
                [('x_bitrix_message_id', '!=', False)]),
            'Comments without author mapping': self.env['mail.message'].sudo().search_count(
                [('x_bitrix_message_id', '!=', False), ('x_bitrix_author_id', '!=', False)]),
            'Attachments': self.env['ir.attachment'].sudo().search_count([]),
            'Mapping records': self.env['bitrix.migration.mapping'].sudo().search_count([]),
            'Departments': self.env['hr.department'].sudo().search_count(
                [('x_bitrix_id', '!=', 0)]),
            'Employees': self.env['hr.employee'].sudo().with_context(active_test=False).search_count(
                [('x_bitrix_id', '!=', 0)]),
        }
        if self.fallback_project_id:
            checks['Tasks in fallback project'] = self.env['project.task'].sudo().with_context(
                active_test=False,
            ).search_count([
                ('x_bitrix_id', '!=', False),
                ('project_id', '=', self.fallback_project_id.id),
            ])
        for label, count in checks.items():
            self._append_log(f'  {label}: {count}')

    def _normalize_login(self, value, fallback='bitrix_user'):
        value = (value or '').strip().lower()
        value = re.sub(r'[^a-z0-9@._+-]+', '_', value)
        value = value.strip('._-')
        return value or fallback

    def _make_unique_login(self, preferred_login, fallback_login):
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        base_login = self._normalize_login(preferred_login, fallback=fallback_login)
        login = base_login
        suffix = 1
        while Users.search_count([('login', '=', login)]):
            login = f'{base_login}_{suffix}'
            suffix += 1
        return login

    def _find_existing_user_for_employee(self, employee):
        Users = self.env['res.users'].sudo().with_context(active_test=False)

        if employee.user_id:
            return employee.user_id

        if 'work_contact_id' in employee._fields and employee.work_contact_id:
            user = Users.search([('partner_id', '=', employee.work_contact_id.id)], limit=1)
            if user:
                return user

        email = (employee.work_email or '').strip().lower()
        if email:
            user = Users.search([('login', '=ilike', email)], limit=1)
            if user:
                return user

        return Users


    def action_send_password_reset(self):
        self.ensure_one()
        self.state = 'running'
        self._append_log('=== Password reset mailing started ===')
        self.progress = 0.0
        self.env.cr.commit()

        try:
            if 'action_reset_password' not in self.env['res.users']._fields and not hasattr(type(self.env['res.users']), 'action_reset_password'):
                raise ValueError('auth_signup reset password flow is not available in this Odoo instance')

            employees = self.env['hr.employee'].sudo().with_context(active_test=False).search([
                ('x_bitrix_id', '!=', 0),
                ('user_id', '!=', False),
            ])
            users = employees.mapped('user_id').sudo().with_context(active_test=False)

            self._append_log(f'Found {len(users)} employee users linked to imported employees')

            missing_email_users = users.filtered(lambda u: not u.email)
            archived_users = users.filtered(lambda u: not u.active)
            ready_users = (users - missing_email_users - archived_users)

            if missing_email_users:
                self._append_log(
                    f'Skipping users without email: {len(missing_email_users)}'
                )
            if archived_users:
                self._append_log(
                    f'Skipping archived users: {len(archived_users)}'
                )

            sent_count = 0
            error_count = 0
            batch_size = 50
            ready_list = ready_users.ids

            for start in range(0, len(ready_list), batch_size):
                batch_ids = ready_list[start:start + batch_size]
                batch_users = self.env['res.users'].sudo().browse(batch_ids).exists()
                try:
                    batch_users.action_reset_password()
                    sent_count += len(batch_users)
                    self._append_log(
                        f'Sent password reset emails: {sent_count}/{len(ready_list)}'
                    )
                    self.env.cr.commit()
                except Exception as e:
                    error_count += len(batch_users)
                    self._append_log(
                        f'ERROR sending reset for batch starting with user_id={batch_users[:1].id if batch_users else "n/a"}: {e}'
                    )

            self.state = 'done'
            self.progress = 100.0
            self._append_log(
                '=== Password reset mailing completed: '
                f'sent={sent_count}, skipped_no_email={len(missing_email_users)}, '
                f'skipped_archived={len(archived_users)}, errors={error_count} ==='
            )

        except Exception:
            self.env.cr.rollback()
            self.state = 'error'
            self._append_log(f'=== PASSWORD RESET ERROR ===\n{traceback.format_exc()}')
            _logger.exception('Password reset mailing failed')

        self.env.cr.commit()

    def _prepare_employee_user_creation(self):
        from ..services.loaders.employees import EmployeeLoader

        group_user = self.env.ref('base.group_user')
        task_employee_group = self.env.ref('bitrix_migration.group_bitrix_task_employee')
        project_group_user = self.env.ref('project.group_project_user')
        project_group_manager = self.env.ref('project.group_project_manager')
        mapping = self.env['bitrix.migration.mapping'].sudo()
        users_model = self.env['res.users'].sudo().with_context(
            active_test=False,
            no_reset_password=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
        )
        sync_helper = EmployeeLoader(self.env, extractor=None, log_callback=self._append_log)
        return group_user, task_employee_group, project_group_user, project_group_manager, mapping, users_model, sync_helper

    def _sync_imported_project_visibility(self):
        Project = self.env['project.project'].sudo().with_context(active_test=False)
        projects = Project.search([
            ('x_bitrix_id', '!=', False),
            ('privacy_visibility', '!=', 'followers'),
        ])
        if projects:
            projects.write({'privacy_visibility': 'followers'})
            self._append_log(
                f'Set follower-only visibility on imported projects: {len(projects)}'
            )

    def _sync_imported_stage_ownership(self):
        Stage = self.env['project.task.type'].sudo().with_context(active_test=False)
        stages = Stage.search([
            ('x_bitrix_id', '!=', False),
            ('user_id', '!=', False),
        ])
        if stages:
            stages.write({'user_id': False})
            self._append_log(
                f'Reset stage owners for imported Bitrix stages: {len(stages)}'
            )

    def _ensure_user_access_groups(self, user, group_user, task_employee_group, project_group_user, project_group_manager):
        target_group_ids = {group_user.id, task_employee_group.id}
        current_group_ids = set(user.group_ids.ids)
        commands = []

        for group_id in sorted(target_group_ids - current_group_ids):
            commands.append((4, group_id))

        if (
            project_group_user.id in current_group_ids
            and project_group_manager.id not in current_group_ids
        ):
            commands.append((3, project_group_user.id))

        if commands:
            user.write({'group_ids': commands})

    def _ensure_user_for_employee(self, employee, group_user, task_employee_group, project_group_user, project_group_manager, mapping, users_model, sync_helper):
        existing_user = self._find_existing_user_for_employee(employee)

        if existing_user:
            self._ensure_user_access_groups(existing_user, group_user, task_employee_group, project_group_user, project_group_manager)

            if not employee.user_id or employee.user_id != existing_user:
                employee.write({'user_id': existing_user.id})
                status = 'linked'
            else:
                status = 'skipped'

            mapping.set_mapping(
                str(employee.x_bitrix_id),
                'user',
                'res.partner',
                existing_user.partner_id.id,
            )
            sync_helper._sync_related_records(employee)
            return status, existing_user

        company_id = employee.company_id.id if 'company_id' in employee._fields and employee.company_id else self.env.company.id
        preferred_login = (employee.work_email or '').strip().lower()
        login = self._make_unique_login(preferred_login, f'bitrix_{employee.x_bitrix_id}')
        user_vals = {
            'name': employee.name,
            'login': login,
            'email': preferred_login or False,
            'company_id': company_id,
            'company_ids': [(6, 0, [company_id])],
            'group_ids': [(6, 0, [group_user.id, task_employee_group.id])],
            'notification_type': 'inbox',
            'active': employee.active if 'active' in employee._fields else True,
        }

        if 'work_contact_id' in employee._fields and employee.work_contact_id:
            user_vals['partner_id'] = employee.work_contact_id.id

        user = users_model.create(user_vals)
        self._ensure_user_access_groups(user, group_user, task_employee_group, project_group_user, project_group_manager)
        employee.write({'user_id': user.id})
        mapping.set_mapping(
            str(employee.x_bitrix_id),
            'user',
            'res.partner',
            user.partner_id.id,
        )
        sync_helper._sync_related_records(employee)
        return 'created', user

    def action_create_test_employee_user(self):
        self.ensure_one()
        if not self.test_employee_id:
            self._append_log('ERROR: select a test employee first')
            return

        employee = self.test_employee_id.sudo().with_context(active_test=False)
        self.state = 'running'
        self._append_log(
            f'=== Test employee user creation started: {employee.name} (bitrix_id={employee.x_bitrix_id}) ==='
        )
        self.progress = 0.0
        self.env.cr.commit()

        try:
            self._sync_imported_project_visibility()
            self._sync_imported_stage_ownership()
            group_user, task_employee_group, project_group_user, project_group_manager, mapping, users_model, sync_helper = self._prepare_employee_user_creation()
            with self.env.cr.savepoint():
                status, user = self._ensure_user_for_employee(
                    employee, group_user, task_employee_group, project_group_user, project_group_manager, mapping, users_model, sync_helper,
                )
            self.state = 'done'
            self.progress = 100.0
            self._append_log(
                f'=== Test employee user creation completed: status={status}, user_id={user.id}, login={user.login} ==='
            )
        except Exception:
            self.env.cr.rollback()
            self.state = 'error'
            self._append_log(f'=== TEST USER CREATION ERROR ===\n{traceback.format_exc()}')
            _logger.exception('Test employee user creation failed')

        self.env.cr.commit()

    def action_create_employee_users(self):
        self.ensure_one()
        self.state = 'running'
        self._append_log('=== Employee user creation started ===')
        self.progress = 0.0
        self.env.cr.commit()

        try:
            self._sync_imported_project_visibility()
            self._sync_imported_stage_ownership()
            group_user, task_employee_group, project_group_user, project_group_manager, mapping, users_model, sync_helper = self._prepare_employee_user_creation()
            Employee = self.env['hr.employee'].sudo().with_context(active_test=False)
            employees = Employee.search([('x_bitrix_id', '!=', 0)])
            self._append_log(f'Found {len(employees)} imported employees')

            created_count = 0
            linked_count = 0
            skipped_count = 0
            error_count = 0

            for index, employee in enumerate(employees, start=1):
                try:
                    with self.env.cr.savepoint():
                        status, _user = self._ensure_user_for_employee(
                            employee, group_user, task_employee_group, project_group_user, project_group_manager, mapping, users_model, sync_helper,
                        )
                        if status == 'created':
                            created_count += 1
                        elif status == 'linked':
                            linked_count += 1
                        else:
                            skipped_count += 1

                    if index % 25 == 0:
                        self._append_log(
                            f'Processed {index}/{len(employees)} employees: '
                            f'created={created_count}, linked={linked_count}, '
                            f'skipped={skipped_count}, errors={error_count}'
                        )
                        self.env.cr.commit()

                except Exception as e:
                    error_count += 1
                    self._append_log(
                        f'ERROR employee bitrix_id={employee.x_bitrix_id} ({employee.name}): {e}'
                    )

            self.state = 'done'
            self.progress = 100.0
            self._append_log(
                '=== Employee user creation completed: '
                f'created={created_count}, linked={linked_count}, '
                f'skipped={skipped_count}, errors={error_count} ==='
            )

        except Exception:
            self.env.cr.rollback()
            self.state = 'error'
            self._append_log(f'=== USER CREATION ERROR ===\n{traceback.format_exc()}')
            _logger.exception('Employee user creation failed')

        self.env.cr.commit()

    def _purge_records_by_domain(self, model_name, domain, label, batch_size=500):
        Model = self.env[model_name].sudo().with_context(active_test=False)
        deleted = 0

        while True:
            records = Model.search(domain, limit=batch_size)
            if not records:
                break

            batch_count = len(records)
            records.unlink()
            deleted += batch_count
            self._append_log(f'Purged {label}: {deleted}')
            self.env.cr.commit()

        return deleted

    def _purge_records_by_mapping(self, entity_type, model_name, label, batch_size=500):
        Mapping = self.env['bitrix.migration.mapping'].sudo()
        Model = self.env[model_name].sudo().with_context(active_test=False)
        deleted = 0

        while True:
            mappings = Mapping.search([
                ('entity_type', '=', entity_type),
                ('odoo_model', '=', model_name),
            ], limit=batch_size)
            if not mappings:
                break

            records = Model.browse(mappings.mapped('odoo_id')).exists()
            if records:
                batch_count = len(records)
                records.unlink()
                deleted += batch_count
            else:
                batch_count = 0

            mappings.unlink()
            if batch_count:
                self._append_log(f'Purged {label}: {deleted}')
            self.env.cr.commit()

        return deleted

    def action_purge_data(self):
        self.ensure_one()
        self.state = 'running'
        self.log_output = '=== Purge started ==='
        self.progress = 0.0
        self.env.cr.commit()

        try:
            self._append_log('Removing imported attachments...')
            self._purge_records_by_mapping('attachment', 'ir.attachment', 'attachments')

            self._append_log('Removing imported comments...')
            self._purge_records_by_domain(
                'mail.message',
                [('x_bitrix_message_id', '!=', False)],
                'comments',
            )

            self._append_log('Removing imported meetings...')
            self._purge_records_by_domain(
                'calendar.event',
                [('x_bitrix_id', '!=', False)],
                'meetings',
            )

            self._append_log('Removing imported tasks...')
            self._purge_records_by_domain(
                'project.task',
                [('x_bitrix_id', '!=', False)],
                'tasks',
            )

            self._append_log('Removing imported stages...')
            self._purge_records_by_domain(
                'project.task.type',
                [('x_bitrix_id', '!=', False)],
                'stages',
            )

            self._append_log('Removing imported projects...')
            self._purge_records_by_domain(
                'project.project',
                [('x_bitrix_id', '!=', False)],
                'projects',
            )

            self._append_log('Removing imported tags...')
            self._purge_records_by_mapping('tag', 'project.tags', 'tags')

            self._append_log('Clearing migration mappings and checkpoints...')
            self.env['bitrix.migration.mapping'].sudo().search([]).unlink()
            self._clear_checkpoints()

            self.state = 'draft'
            self.progress = 0.0
            self._append_log('=== Purge completed successfully ===')

        except Exception:
            self.env.cr.rollback()
            self.state = 'error'
            self._append_log(f'=== PURGE ERROR ===\n{traceback.format_exc()}')
            _logger.exception('Migration purge failed')

        self.env.cr.commit()

    def _clear_checkpoints(self):
        checkpoint_keys = [
            'user', 'tag', 'project', 'stage', 'task', 'task_relink',
            'comment', 'attachment', 'department', 'employee',
        ]
        params = self.env['ir.config_parameter'].sudo()
        for key in checkpoint_keys:
            params.set_param(f'bitrix_migration.checkpoint.{key}', '')
        self.env.cr.commit()

    def action_reset(self):
        self.ensure_one()
        self._clear_checkpoints()
        self.state = 'draft'
        self.log_output = ''
        self.progress = 0.0

    @api.model
    def get_singleton_action(self):
        record = self.search([], limit=1, order='id asc')
        if not record:
            record = self.create({'mode': 'dry_run'})
        return {
            'type': 'ir.actions.act_window',
            'name': 'Bitrix Migration',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': record.id,
            'target': 'current',
        }
