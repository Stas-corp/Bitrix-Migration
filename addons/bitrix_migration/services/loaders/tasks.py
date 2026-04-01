import logging

from ..normalizers.dto import BitrixTask
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class TaskLoader(BaseLoader):
    """Loads Bitrix tasks into project.task (pass 1: without parent_id)."""

    entity_type = 'task'
    batch_size = 1000

    def __init__(self, env, extractor, fallback_project_id=None, **kwargs):
        super().__init__(env, extractor, **kwargs)
        self.fallback_project_id = fallback_project_id
        self.fallback_count = 0

    def _sync_project_and_stage(self, record, task, project_map, stage_map):
        target_project_id = False
        if task.project_external_id:
            target_project_id = project_map.get(str(task.project_external_id)) or False
        if not target_project_id and self.fallback_project_id:
            target_project_id = self.fallback_project_id

        target_stage_id = False
        if target_project_id and task.stage_id:
            target_stage_id = stage_map.get(str(task.stage_id)) or False

        vals = {}
        current_project_id = record.project_id.id if record.project_id else False
        current_stage_id = record.stage_id.id if record.stage_id else False

        if current_project_id != target_project_id:
            vals['project_id'] = target_project_id
        if current_stage_id != target_stage_id:
            vals['stage_id'] = target_stage_id

        if vals:
            record.write(vals)

    def _sync_tags(self, record, task, tag_name_map):
        if not task.tags:
            return

        tag_names = [tag.strip() for tag in task.tags.split(',') if tag.strip()]
        target_tag_ids = [
            tag_name_map[tag_name.lower()]
            for tag_name in tag_names
            if tag_name.lower() in tag_name_map
        ]
        if target_tag_ids and set(record.tag_ids.ids) != set(target_tag_ids):
            record.write({'tag_ids': [(6, 0, target_tag_ids)]})

    def _sync_created_at(self, record, task):
        if not task.date_created:
            return

        if self.db_column_exists('project_task', 'x_bitrix_created_at'):
            current_created_at = record.x_bitrix_created_at
            if current_created_at != task.date_created:
                record.write({'x_bitrix_created_at': task.date_created})
        else:
            self.log_once(
                'missing_project_task_x_bitrix_created_at',
                'Skipping project_task.x_bitrix_created_at storage: column is missing. '
                'Upgrade the bitrix_migration module to persist Bitrix task creation date.',
            )

        self.env.cr.execute(
            """
            UPDATE project_task
            SET create_date = %s
            WHERE id = %s
              AND (create_date IS NULL OR create_date != %s)
            """,
            (task.date_created, record.id, task.date_created),
        )
        record.invalidate_recordset(['create_date'])

    @staticmethod
    def _split_bitrix_user_ids(raw_ids):
        if not raw_ids:
            return []
        return [uid.strip() for uid in raw_ids.split(',') if uid and uid.strip()]

    @staticmethod
    def _merge_bitrix_user_ids(*groups):
        merged = []
        for group in groups:
            for uid in group:
                if uid not in merged:
                    merged.append(uid)
        return merged

    def _resolve_task_users(self, bitrix_user_ids, user_map, employee_map):
        employee_ids = []
        user_ids = []

        for uid_str in bitrix_user_ids:
            employee = self.find_employee_by_bitrix_id(uid_str, employee_map=employee_map)
            if employee and employee.id not in employee_ids:
                employee_ids.append(employee.id)

                user = self.get_user_from_employee(employee)
                if user and user.id not in user_ids:
                    user_ids.append(user.id)
                    continue

            partner_id = user_map.get(uid_str)
            if partner_id:
                odoo_user = self.env['res.users'].sudo().search(
                    [('partner_id', '=', partner_id)], limit=1,
                )
                if odoo_user and odoo_user.id not in user_ids:
                    user_ids.append(odoo_user.id)

        return employee_ids, user_ids

    def _sync_employee_links(self, record, field_name, rel_table_name, employee_ids, warning_key, warning_message):
        if field_name not in record._fields:
            return

        if not self.db_table_exists(rel_table_name):
            self.log_once(warning_key, warning_message)
            return

        current_employee_ids = set(record[field_name].ids)
        target_employee_ids = set(employee_ids)
        if current_employee_ids != target_employee_ids:
            record.write({field_name: [(6, 0, sorted(target_employee_ids))]})

    def _sync_assignees(self, record, task, user_map, employee_map):
        responsible_bitrix_ids = self._split_bitrix_user_ids(task.responsible_user_ids)
        auditor_bitrix_ids = self._split_bitrix_user_ids(task.auditor_user_ids)
        creator_bitrix_ids = [str(task.creator_bitrix_id)] if task.creator_bitrix_id else []

        participant_bitrix_ids = self._merge_bitrix_user_ids(
            responsible_bitrix_ids,
            auditor_bitrix_ids,
            creator_bitrix_ids,
        )

        responsible_employee_ids, _ = self._resolve_task_users(
            responsible_bitrix_ids, user_map, employee_map,
        )
        participant_employee_ids, user_ids = self._resolve_task_users(
            participant_bitrix_ids, user_map, employee_map,
        )

        self._sync_employee_links(
            record,
            'x_bitrix_responsible_employee_ids',
            'project_task_bitrix_employee_rel',
            responsible_employee_ids,
            'missing_project_task_bitrix_employee_rel',
            'Skipping employee task links: relation table '
            '"project_task_bitrix_employee_rel" is missing. '
            'Upgrade the bitrix_migration module to enable employee-based history.',
        )
        self._sync_employee_links(
            record,
            'x_bitrix_participant_employee_ids',
            'project_task_bitrix_participant_rel',
            participant_employee_ids,
            'missing_project_task_bitrix_participant_rel',
            'Skipping participant task links: relation table '
            '"project_task_bitrix_participant_rel" is missing. '
            'Upgrade the bitrix_migration module to enable full participant sync.',
        )

        task_fields = record._fields
        if 'user_ids' in task_fields:
            target_user_ids = sorted(set(user_ids))
            if set(record.user_ids.ids) != set(target_user_ids):
                record.write({'user_ids': [(6, 0, target_user_ids)]})
        elif 'user_id' in task_fields:
            target_user_id = user_ids[0] if user_ids else False
            if (record.user_id.id if record.user_id else False) != target_user_id:
                record.write({'user_id': target_user_id})

    def _sync_creator(self, record, task, employee_map):
        """Set x_bitrix_creator_employee_id from task.creator_bitrix_id."""
        if not task.creator_bitrix_id:
            return
        if 'x_bitrix_creator_employee_id' not in record._fields:
            self.log_once(
                'missing_x_bitrix_creator_employee_id',
                'Skipping creator storage: field x_bitrix_creator_employee_id is missing. '
                'Upgrade the bitrix_migration module.',
            )
            return

        employee = self.find_employee_by_bitrix_id(
            str(task.creator_bitrix_id), employee_map=employee_map,
        )
        if not employee:
            return

        current_id = record.x_bitrix_creator_employee_id.id if record.x_bitrix_creator_employee_id else False
        if current_id != employee.id:
            record.write({'x_bitrix_creator_employee_id': employee.id})

    def run(self, raw_tasks=None):
        """Load tasks. Optionally accepts pre-fetched raw_tasks list."""
        if raw_tasks is None:
            self.log('Extracting Bitrix tasks...')
            raw_tasks = self.extractor.get_tasks()
        self.log(f'Found {len(raw_tasks)} tasks')

        project_map = self.get_mapping().get_all_mappings('project')
        stage_map = self.get_mapping().get_all_mappings('stage')
        user_map = self.get_mapping().get_all_mappings('user')
        employee_map = self.get_mapping().get_all_mappings('employee')
        tag_name_map = self._build_tag_name_map()

        checkpoint = self.get_checkpoint()
        skip_until = int(checkpoint) if checkpoint else 0

        processed = 0
        for batch in self._batched(raw_tasks, self.batch_size):
            last_task_id = None

            for row in batch:
                task = BitrixTask(**row)
                last_task_id = task.external_id

                if task.external_id <= skip_until:
                    continue

                bid = str(task.external_id)
                vals = {
                    'name': task.name,
                    'description': task.description or '',
                    'x_bitrix_id': bid,
                    'x_bitrix_stage_id': str(task.stage_id) if task.stage_id else '',
                    'x_bitrix_parent_id': str(task.parent_id) if task.parent_id else '',
                }
                if 'user_ids' in self.env['project.task']._fields:
                    vals['user_ids'] = [(6, 0, [])]
                elif 'user_id' in self.env['project.task']._fields:
                    vals['user_id'] = False

                odoo_proj_id = False
                if task.project_external_id:
                    odoo_proj_id = project_map.get(str(task.project_external_id))
                if odoo_proj_id:
                    vals['project_id'] = odoo_proj_id
                elif self.fallback_project_id:
                    vals['project_id'] = self.fallback_project_id
                    self.fallback_count += 1

                if task.stage_id and odoo_proj_id:
                    odoo_stage_id = stage_map.get(str(task.stage_id))
                    if odoo_stage_id:
                        vals['stage_id'] = odoo_stage_id

                if task.date_deadline:
                    vals['date_deadline'] = task.date_deadline.strftime('%Y-%m-%d')
                if task.date_created and self.db_column_exists('project_task', 'x_bitrix_created_at'):
                    vals['x_bitrix_created_at'] = task.date_created

                record, created = self.get_or_create(
                    'project.task',
                    [('x_bitrix_id', '=', bid)],
                    vals,
                    bitrix_id=task.external_id,
                    entity_type='task',
                )

                if record and not self.dry_run:
                    self._sync_project_and_stage(record, task, project_map, stage_map)
                    self._sync_created_at(record, task)
                    self._sync_tags(record, task, tag_name_map)
                    self._sync_assignees(record, task, user_map, employee_map)
                    self._sync_creator(record, task, employee_map)

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=last_task_id)

        if self.fallback_count:
            self.log(f'Tasks assigned to fallback project: {self.fallback_count}')
        self.log_stats()

    def _build_tag_name_map(self):
        tags = self.env['project.tags'].sudo().search([])
        return {tag.name.lower(): tag.id for tag in tags if tag.name}
