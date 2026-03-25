import logging

from .base import BaseLoader

_logger = logging.getLogger(__name__)


class TaskRelinkLoader(BaseLoader):
    """Pass 2: sets parent_id on tasks that have PARENT_ID in Bitrix."""

    entity_type = 'task_relink'
    batch_size = 2000

    def run(self):
        self.log('Relinking parent tasks...')

        task_map = self.get_mapping().get_all_mappings('task')

        # Find all tasks with x_bitrix_parent_id set
        tasks_with_parent = self.env['project.task'].sudo().with_context(active_test=False).search_read(
            [('x_bitrix_parent_id', '!=', False), ('x_bitrix_parent_id', '!=', '')],
            ['id', 'x_bitrix_id', 'x_bitrix_parent_id', 'parent_id'],
        )
        self.log(f'Found {len(tasks_with_parent)} tasks with parent references')

        processed = 0
        for batch in self._batched(tasks_with_parent, self.batch_size):
            for rec in batch:
                if rec['parent_id']:
                    # Already linked
                    self.skipped_count += 1
                    processed += 1
                    continue

                parent_bitrix_id = rec['x_bitrix_parent_id']
                parent_odoo_id = task_map.get(parent_bitrix_id)

                if not parent_odoo_id:
                    self.error_count += 1
                    self.errors.append((
                        rec['x_bitrix_id'],
                        f'Parent bitrix_id={parent_bitrix_id} not found in mapping',
                    ))
                    processed += 1
                    continue

                if not self.dry_run:
                    try:
                        self.env['project.task'].sudo().browse(rec['id']).write({
                            'parent_id': parent_odoo_id,
                        })
                        self.updated_count += 1
                    except Exception as e:
                        self.error_count += 1
                        self.errors.append((rec['x_bitrix_id'], str(e)))
                else:
                    self.updated_count += 1

                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()
