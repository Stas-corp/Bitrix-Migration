import logging

from ..normalizers.dto import BitrixTask
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class TaskLoader(BaseLoader):
    """Loads Bitrix tasks into project.task (pass 1: without parent_id)."""

    entity_type = 'task'
    batch_size = 1000

    def run(self, raw_tasks=None):
        """Load tasks. Optionally accepts pre-fetched raw_tasks list."""
        if raw_tasks is None:
            self.log('Extracting Bitrix tasks...')
            raw_tasks = self.extractor.get_tasks()
        self.log(f'Found {len(raw_tasks)} tasks')

        # Pre-load mappings for O(1) lookups
        project_map = self.get_mapping().get_all_mappings('project')
        stage_map = self.get_mapping().get_all_mappings('stage')
        user_map = self.get_mapping().get_all_mappings('user')
        tag_name_map = self._build_tag_name_map()

        # Pre-fetch existing x_bitrix_id to skip already-migrated tasks
        existing_ids = set()
        existing_recs = self.env['project.task'].sudo().with_context(active_test=False).search_read(
            [('x_bitrix_id', '!=', False)], ['x_bitrix_id'],
        )
        for r in existing_recs:
            if r['x_bitrix_id']:
                existing_ids.add(r['x_bitrix_id'])

        # Checkpoint resume
        checkpoint = self.get_checkpoint()
        skip_until = int(checkpoint) if checkpoint else 0

        processed = 0
        for batch in self._batched(raw_tasks, self.batch_size):
            for row in batch:
                task = BitrixTask(**row)

                if task.external_id <= skip_until:
                    continue

                bid = str(task.external_id)
                if bid in existing_ids:
                    self.skipped_count += 1
                    processed += 1
                    continue

                vals = {
                    'name': task.name,
                    'description': task.description or '',
                    'x_bitrix_id': bid,
                    'x_bitrix_stage_id': str(task.stage_id) if task.stage_id else '',
                    'x_bitrix_parent_id': str(task.parent_id) if task.parent_id else '',
                }

                # Resolve project
                if task.project_external_id:
                    odoo_proj_id = project_map.get(str(task.project_external_id))
                    if odoo_proj_id:
                        vals['project_id'] = odoo_proj_id

                # Resolve stage
                if task.stage_id:
                    odoo_stage_id = stage_map.get(str(task.stage_id))
                    if odoo_stage_id:
                        vals['stage_id'] = odoo_stage_id

                # Resolve deadline
                if task.date_deadline:
                    vals['date_deadline'] = task.date_deadline.strftime('%Y-%m-%d')

                # parent_id is intentionally NOT set here — done in tasks_relink pass 2

                record, created = self.get_or_create(
                    'project.task',
                    [('x_bitrix_id', '=', bid)],
                    vals,
                    bitrix_id=task.external_id,
                    entity_type='task',
                )

                if created and record and not self.dry_run:
                    existing_ids.add(bid)

                    # Resolve tags
                    if task.tags:
                        tag_names = [t.strip() for t in task.tags.split(',') if t.strip()]
                        tag_ids = [tag_name_map[tn.lower()] for tn in tag_names if tn.lower() in tag_name_map]
                        if tag_ids:
                            record.write({'tag_ids': [(6, 0, tag_ids)]})

                    # Resolve assignees
                    if task.responsible_user_ids:
                        uid_strs = [u.strip() for u in task.responsible_user_ids.split(',') if u.strip()]
                        partner_ids = []
                        for uid_str in uid_strs:
                            pid = user_map.get(uid_str)
                            if pid:
                                odoo_user = self.env['res.users'].sudo().search(
                                    [('partner_id', '=', pid)], limit=1,
                                )
                                if odoo_user:
                                    partner_ids.append(odoo_user.id)
                        if partner_ids:
                            # Discover field name for assignees
                            task_fields = self.env['project.task']._fields
                            if 'user_ids' in task_fields:
                                record.write({'user_ids': [(6, 0, partner_ids)]})
                            elif 'user_id' in task_fields:
                                record.write({'user_id': partner_ids[0]})

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=task.external_id)

        self.log_stats()

    def _build_tag_name_map(self):
        tags = self.env['project.tags'].sudo().search([])
        return {t.name.lower(): t.id for t in tags if t.name}
