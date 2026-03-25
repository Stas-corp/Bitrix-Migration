import logging

from ..normalizers.dto import BitrixTag
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class TagLoader(BaseLoader):
    """Loads tags into project.tags, deduplicated by name (case-insensitive)."""

    entity_type = 'tag'
    batch_size = 500

    def run(self):
        self.log('Extracting Bitrix tags...')
        raw_tags = self.extractor.get_tags()
        self.log(f'Found {len(raw_tags)} tags')

        processed = 0
        for batch in self._batched(raw_tags, self.batch_size):
            for row in batch:
                tag = BitrixTag(**row)
                if not tag.name:
                    self.skipped_count += 1
                    continue

                existing = self.env['project.tags'].sudo().search(
                    [('name', '=ilike', tag.name)], limit=1,
                )
                if existing:
                    if not self.dry_run:
                        self.get_mapping().set_mapping(
                            str(tag.id), 'tag', 'project.tags', existing.id,
                        )
                    self.skipped_count += 1
                else:
                    if not self.dry_run:
                        new_tag = self.env['project.tags'].sudo().create({
                            'name': tag.name,
                        })
                        self.get_mapping().set_mapping(
                            str(tag.id), 'tag', 'project.tags', new_tag.id,
                        )
                    self.created_count += 1

                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()
