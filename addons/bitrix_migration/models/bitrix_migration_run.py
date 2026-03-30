import logging
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

    # Mode-specific
    pilot_project_ids = fields.Char(string='Pilot Project IDs (comma-separated)')
    single_task_bitrix_id = fields.Char(string='Single Task Bitrix ID')

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
        self.log_output = (self.log_output or '') + '\n' + message
        self.env.cr.commit()

    def _get_extractor(self):
        from ..services.extractors.bitrix_mysql import BitrixMySQLExtractor
        return BitrixMySQLExtractor(
            host=self.mysql_host,
            port=self.mysql_port,
            user=self.mysql_user,
            password=self.mysql_password,
            database=self.mysql_database,
        )

    def action_run(self):
        self.ensure_one()
        self.state = 'running'
        self.log_output = f'=== Migration started: mode={self.mode} ==='
        self.progress = 0.0
        self.env.cr.commit()

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

            extractor.close()
            self.state = 'done'
            self.progress = 100.0
            self._append_log('=== Migration completed successfully ===')

        except Exception as e:
            self.state = 'error'
            self._append_log(f'=== ERROR ===\n{traceback.format_exc()}')
            _logger.exception('Migration failed')

        self.env.cr.commit()

    def _run_dry_run(self, extractor):
        """Count all entities without writing to Odoo."""
        self._append_log('--- DRY RUN: counting entities ---')
        counts = {
            'Projects': extractor.count_projects(),
            'Tasks': extractor.count_tasks(),
            'Tags': extractor.count_tags(),
            'Stages (G)': extractor.count_stages(),
            'Comments (real)': extractor.count_comments(),
            'Meetings': extractor.count_meetings(),
        }

        # Odoo existing counts
        odoo_counts = {
            'Projects': self.env['project.project'].sudo().search_count(
                [('x_bitrix_id', '!=', False)]),
            'Tasks': self.env['project.task'].sudo().with_context(active_test=False).search_count(
                [('x_bitrix_id', '!=', False)]),
            'Comments': self.env['mail.message'].sudo().search_count(
                [('x_bitrix_message_id', '!=', False)]),
        }

        self._append_log(f"{'Entity':<20} {'Bitrix':>10} {'Odoo':>10} {'To Create':>10}")
        self._append_log('-' * 55)
        for entity, cnt in counts.items():
            odoo_cnt = odoo_counts.get(entity, 0)
            self._append_log(f'{entity:<20} {cnt:>10} {odoo_cnt:>10} {cnt - odoo_cnt:>10}')

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
            ('Tasks', TaskLoader, {}),
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
                    from ..services.normalizers.dto import BitrixProject
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
            raw_stages = [s for s in extractor.get_stages() if s['ID'] == stage_id]
            if raw_stages:
                stage_loader = StageLoader(self.env, extractor, log_callback=self._append_log)
                for row in raw_stages:
                    from ..services.normalizers.dto import BitrixStage
                    stg = BitrixStage(**row)
                    vals = {
                        'name': stg.name,
                        'x_bitrix_id': str(stg.id),
                        'x_bitrix_entity_id': str(stg.entity_id),
                    }
                    stage_loader.get_or_create(
                        'project.task.type',
                        [('x_bitrix_id', '=', str(stg.id))],
                        vals,
                        bitrix_id=stg.id,
                        entity_type='stage',
                    )
                stage_loader.commit_checkpoint(1)

        # 6. Task itself
        self._append_log(f'\n--- Task ---')
        task_loader = TaskLoader(self.env, extractor, log_callback=self._append_log)
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
        }
        for label, count in checks.items():
            self._append_log(f'  {label}: {count}')

    def action_reset(self):
        self.ensure_one()
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
