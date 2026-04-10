import logging

from ..normalizers.dto import BitrixTask
from ..normalizers.bitrix_markup import normalize_bitrix_markup, build_employee_name_map
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class TaskLoader(BaseLoader):
    """Loads Bitrix tasks into project.task (pass 1: without parent_id)."""

    entity_type = 'task'
    batch_size = 1000
    FALLBACK_STAGE_SPECS = (
        ('Чекає виконання', 0, False),
        ('Виконується', 1, False),
        ('Чекає контролю', 2, False),
        ('Відкладене', 3, False),
        ('Завершене', 4, True),
        ('Скасована', 5, True),
    )
    DEFAULT_FALLBACK_STAGE_NAME = 'Чекає виконання'
    STATUS_TO_FALLBACK_STAGE = {
        1: 'Чекає виконання',
        2: 'Чекає виконання',
        3: 'Виконується',
        4: 'Чекає контролю',
        5: 'Завершене',
        6: 'Відкладене',
        7: 'Скасована',
    }

    def __init__(self, env, extractor, fallback_project_id=None, fallback_stage_ids=None, **kwargs):
        super().__init__(env, extractor, **kwargs)
        self.fallback_project_id = fallback_project_id
        self.fallback_stage_ids = fallback_stage_ids or {}
        self.fallback_count = 0
        self._project_follower_cache = {}

    @classmethod
    def get_fallback_stage_name_for_status(cls, status_code):
        try:
            status_int = int(status_code)
        except (TypeError, ValueError):
            return cls.DEFAULT_FALLBACK_STAGE_NAME
        return cls.STATUS_TO_FALLBACK_STAGE.get(status_int, cls.DEFAULT_FALLBACK_STAGE_NAME)

    def _resolve_no_project_stage_id(self, task):
        if not self.fallback_stage_ids:
            return False
        stage_name = self.get_fallback_stage_name_for_status(task.status_code)
        return self.fallback_stage_ids.get(stage_name) or self.fallback_stage_ids.get(
            self.DEFAULT_FALLBACK_STAGE_NAME
        )

    def _build_stage_meta_map(self):
        Stage = self.env['project.task.type'].sudo().with_context(active_test=False)
        stage_meta_map = {}
        for stage in Stage.search([('x_bitrix_id', '!=', False)]):
            entity_type = 'G'
            if 'x_bitrix_entity_type' in stage._fields and stage.x_bitrix_entity_type:
                entity_type = stage.x_bitrix_entity_type
            stage_meta_map[str(stage.x_bitrix_id)] = {
                'id': stage.id,
                'entity_type': entity_type,
                'entity_id': str(stage.x_bitrix_entity_id or ''),
                'project_ids': set(stage.project_ids.ids),
            }
        return stage_meta_map

    def _resolve_project_and_stage(self, task, project_map, stage_meta_map):
        target_project_id = False
        target_stage_id = False

        stage_meta = stage_meta_map.get(str(task.stage_id)) if task.stage_id else None

        if task.project_external_id:
            target_project_id = project_map.get(str(task.project_external_id)) or False
            if target_project_id:
                if stage_meta and stage_meta['entity_type'] == 'G':
                    if target_project_id in stage_meta['project_ids']:
                        target_stage_id = stage_meta['id']
                return target_project_id, target_stage_id
            if self.fallback_project_id:
                self.log_once(
                    f'missing_project_{task.project_external_id}',
                    f'WARNING: Bitrix project {task.project_external_id} not in mapping, '
                    f'task {task.external_id} assigned to fallback project',
                )
                return self.fallback_project_id, self._resolve_no_project_stage_id(task)
            return False, False

        if self.fallback_project_id:
            return self.fallback_project_id, self._resolve_no_project_stage_id(task)

        return False, False

    def _sync_project_and_stage(self, record, task, project_map, stage_meta_map):
        target_project_id, target_stage_id = self._resolve_project_and_stage(
            task, project_map, stage_meta_map,
        )

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

    def _sync_employee_links(self, record, field_name, employee_ids):
        if field_name not in record._fields:
            return

        current_employee_ids = set(record[field_name].ids)
        target_employee_ids = set(employee_ids)
        if current_employee_ids != target_employee_ids:
            record.write({field_name: [(6, 0, sorted(target_employee_ids))]})

    def _sync_employee_m2o_link(self, record, field_name, employee_id):
        if field_name not in record._fields:
            return
        current_id = record[field_name].id if record[field_name] else False
        if current_id != (employee_id or False):
            record.write({field_name: employee_id or False})

    def _sync_assignees(self, record, task, user_map, employee_map):
        # Parse each role separately
        responsible_bitrix_ids = self._split_bitrix_user_ids(task.responsible_user_ids)
        accomplice_bitrix_ids = self._split_bitrix_user_ids(task.accomplice_user_ids)
        auditor_bitrix_ids = self._split_bitrix_user_ids(task.auditor_user_ids)
        originator_bitrix_ids = self._split_bitrix_user_ids(task.originator_user_ids)

        # Resolve employees and users per role
        responsible_employee_ids, responsible_user_ids = self._resolve_task_users(
            responsible_bitrix_ids, user_map, employee_map,
        )
        accomplice_employee_ids, accomplice_user_ids = self._resolve_task_users(
            accomplice_bitrix_ids, user_map, employee_map,
        )
        auditor_employee_ids, _ = self._resolve_task_users(
            auditor_bitrix_ids, user_map, employee_map,
        )
        originator_employee_ids, _ = self._resolve_task_users(
            originator_bitrix_ids, user_map, employee_map,
        )

        # Canonical responsible: take only the first R (deterministic via ORDER BY USER_ID)
        canonical_responsible_eid = responsible_employee_ids[0] if responsible_employee_ids else False
        if len(responsible_employee_ids) > 1:
            self.log_once(
                f'multi_responsible_{record.x_bitrix_id}',
                f'Task {record.x_bitrix_id} has {len(responsible_employee_ids)} source R entries; '
                f'using first (employee_id={canonical_responsible_eid}), ignoring rest',
            )

        # Sync employee links per role
        self._sync_employee_m2o_link(
            record, 'x_bitrix_responsible_employee_id', canonical_responsible_eid,
        )
        self._sync_employee_links(
            record, 'x_bitrix_accomplice_employee_ids', accomplice_employee_ids,
        )
        self._sync_employee_links(
            record, 'x_bitrix_auditor_employee_ids', auditor_employee_ids,
        )
        originator_eid = originator_employee_ids[0] if originator_employee_ids else False
        self._sync_employee_m2o_link(
            record, 'x_bitrix_originator_employee_id', originator_eid,
        )

        # Build canonical assignee user_ids from R + A (includes user_map fallback)
        assignee_user_ids = list(responsible_user_ids)
        for uid in accomplice_user_ids:
            if uid not in assignee_user_ids:
                assignee_user_ids.append(uid)

        # Persist canonical assignees in x_bitrix_assignee_user_ids
        if 'x_bitrix_assignee_user_ids' in record._fields:
            target_sorted = sorted(set(assignee_user_ids))
            if set(record.x_bitrix_assignee_user_ids.ids) != set(target_sorted):
                record.write({'x_bitrix_assignee_user_ids': [(6, 0, target_sorted)]})

        # Mirror to user_ids
        self._recompute_task_user_ids(record)

    def _recompute_task_user_ids(self, record):
        """Recompute user_ids and subscribe project followers."""
        self.recompute_task_user_ids(record)
        # Also subscribe project followers for the resulting user_ids
        if 'user_ids' in record._fields:
            self._subscribe_project_followers(record.project_id, record.user_ids.ids)
        elif 'user_id' in record._fields and record.user_id:
            self._subscribe_project_followers(record.project_id, [record.user_id.id])

    def _subscribe_project_followers(self, project, user_ids):
        if not project or not user_ids:
            return

        users = self.env['res.users'].sudo().browse(user_ids).exists()
        partner_ids = set(users.mapped('partner_id').ids)
        if not partner_ids:
            return

        cached = self._project_follower_cache.get(project.id)
        if cached is None:
            cached = set(project.sudo().message_partner_ids.ids)
            self._project_follower_cache[project.id] = cached

        missing_partner_ids = sorted(partner_ids - cached)
        if missing_partner_ids:
            project.sudo().message_subscribe(partner_ids=missing_partner_ids)
            cached.update(missing_partner_ids)

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
        user_map = self.get_mapping().get_all_mappings('user')
        employee_map = self.get_mapping().get_all_mappings('employee')
        employee_name_map = build_employee_name_map(self.env)
        tag_name_map = self._build_tag_name_map()
        stage_meta_map = self._build_stage_meta_map()

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
                    'description': normalize_bitrix_markup(
                        task.description or '', employee_name_map,
                    ),
                    'x_bitrix_id': bid,
                    'x_bitrix_stage_id': str(task.stage_id) if task.stage_id else '',
                    'x_bitrix_parent_id': str(task.parent_id) if task.parent_id else '',
                }
                if 'user_ids' in self.env['project.task']._fields:
                    vals['user_ids'] = [(6, 0, [])]
                elif 'user_id' in self.env['project.task']._fields:
                    vals['user_id'] = False

                odoo_proj_id, odoo_stage_id = self._resolve_project_and_stage(
                    task, project_map, stage_meta_map,
                )
                if odoo_proj_id:
                    vals['project_id'] = odoo_proj_id
                    if self.fallback_project_id and odoo_proj_id == self.fallback_project_id and not task.project_external_id:
                        self.fallback_count += 1
                elif self.fallback_project_id:
                    vals['project_id'] = self.fallback_project_id
                    self.fallback_count += 1

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
                    self._sync_project_and_stage(
                        record, task, project_map, stage_meta_map,
                    )
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
