import logging

from ..normalizers.dto import BitrixStage
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class StageLoader(BaseLoader):
    """Loads Bitrix G-type stages into project.task.type, linked to projects."""

    entity_type = 'stage'
    batch_size = 500

    def run(self):
        self.log('Extracting Bitrix stages (G-type only)...')
        raw = self.extractor.get_stages()
        self.log(f'Found {len(raw)} G-type stages')

        project_mapping = self.get_mapping().get_all_mappings('project')

        processed = 0
        for batch in self._batched(raw, self.batch_size):
            last_stage_id = None
            for row in batch:
                stage = BitrixStage(**row)
                last_stage_id = stage.id

                vals = {
                    'name': stage.name,
                    'x_bitrix_id': str(stage.id),
                    'x_bitrix_entity_id': str(stage.entity_id),
                }

                record, created = self.get_or_create(
                    'project.task.type',
                    [('x_bitrix_id', '=', str(stage.id))],
                    vals,
                    bitrix_id=stage.id,
                    entity_type='stage',
                )

                # Link stage to project
                if record and not self.dry_run:
                    project_odoo_id = project_mapping.get(str(stage.entity_id))
                    if project_odoo_id:
                        try:
                            record.write({'project_ids': [(4, project_odoo_id)]})
                        except Exception as e:
                            self.log(f'Warning: could not link stage {stage.id} to project {stage.entity_id}: {e}')

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=last_stage_id)

        self.log_stats()
