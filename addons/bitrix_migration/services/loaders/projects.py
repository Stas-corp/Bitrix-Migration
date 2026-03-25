import logging

from ..normalizers.dto import BitrixProject
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class ProjectLoader(BaseLoader):
    """Loads Bitrix projects/workgroups into project.project."""

    entity_type = 'project'
    batch_size = 100

    def run(self):
        self.log('Extracting Bitrix projects...')
        raw = self.extractor.get_projects()
        self.log(f'Found {len(raw)} projects')

        user_mapping = self.get_mapping().get_all_mappings('user')
        tag_name_to_id = self._build_tag_name_map()

        processed = 0
        for batch in self._batched(raw, self.batch_size):
            for row in batch:
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

                if proj.date_start:
                    vals['date_start'] = proj.date_start.strftime('%Y-%m-%d')
                if proj.date_end:
                    vals['date'] = proj.date_end.strftime('%Y-%m-%d')

                # Resolve owner
                if proj.owner_bitrix_id:
                    partner_id = user_mapping.get(str(proj.owner_bitrix_id))
                    if partner_id:
                        user = self.env['res.users'].sudo().search(
                            [('partner_id', '=', partner_id)], limit=1,
                        )
                        if user:
                            vals['user_id'] = user.id

                record, created = self.get_or_create(
                    'project.project',
                    [('x_bitrix_id', '=', str(proj.external_id))],
                    vals,
                    bitrix_id=proj.external_id,
                    entity_type='project',
                )

                # Resolve tags
                if created and record and proj.tags:
                    tag_names = [t.strip() for t in proj.tags.split(',') if t.strip()]
                    tag_ids = []
                    for tn in tag_names:
                        tid = tag_name_to_id.get(tn.lower())
                        if tid:
                            tag_ids.append(tid)
                    if tag_ids and not self.dry_run:
                        record.write({'tag_ids': [(6, 0, tag_ids)]})

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=row.get('external_id'))

        self.log_stats()

    def _build_tag_name_map(self):
        """Build {lowercase_name: odoo_tag_id} from existing project.tags."""
        tags = self.env['project.tags'].sudo().search([])
        return {t.name.lower(): t.id for t in tags if t.name}
