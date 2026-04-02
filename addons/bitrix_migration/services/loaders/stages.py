import logging

from ..normalizers.dto import BitrixStage
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class StageLoader(BaseLoader):
    """Loads Bitrix G/U stages into project.task.type, linked to projects."""

    entity_type = 'stage'
    batch_size = 500

    def _build_personal_project_name(self, owner_bitrix_id):
        bid = str(owner_bitrix_id)
        employee = self.find_employee_by_bitrix_id(bid)
        if employee and employee.name:
            return f'Bitrix: Личные ({employee.name})'

        user_map = self.get_mapping().get_all_mappings('user')
        partner_id = user_map.get(bid)
        if partner_id:
            user = self.env['res.users'].sudo().search([('partner_id', '=', partner_id)], limit=1)
            if user and user.name:
                return f'Bitrix: Личные ({user.name})'

        return f'Bitrix: Личные ({bid})'

    def _resolve_owner_user_id(self, owner_bitrix_id):
        bid = str(owner_bitrix_id)
        employee = self.find_employee_by_bitrix_id(bid)
        user = self.get_user_from_employee(employee)
        if user:
            return user.id

        user_map = self.get_mapping().get_all_mappings('user')
        partner_id = user_map.get(bid)
        if not partner_id:
            return False

        odoo_user = self.env['res.users'].sudo().search([('partner_id', '=', partner_id)], limit=1)
        return odoo_user.id if odoo_user else False

    def _get_or_create_personal_project(self, owner_bitrix_id):
        Project = self.env['project.project'].sudo().with_context(active_test=False)
        owner_bid = str(owner_bitrix_id)
        project = Project.search([
            ('x_bitrix_type', '=', 'personal'),
            ('x_bitrix_owner_bitrix_id', '=', owner_bid),
        ], limit=1)
        if project:
            vals = {}
            if project.privacy_visibility != 'followers':
                vals['privacy_visibility'] = 'followers'
            if not project.active:
                vals['active'] = True
            if vals:
                project.write(vals)
            return project.id

        vals = {
            'name': self._build_personal_project_name(owner_bid),
            'x_bitrix_type': 'personal',
            'x_bitrix_owner_bitrix_id': owner_bid,
            'privacy_visibility': 'followers',
            'active': True,
        }
        owner_user_id = self._resolve_owner_user_id(owner_bid)
        if owner_user_id:
            vals['user_id'] = owner_user_id
        project = Project.create(vals)
        return project.id

    def run(self, raw_stages=None):
        if raw_stages is None:
            self.log('Extracting Bitrix stages (G/U types)...')
            raw = self.extractor.get_stages()
        else:
            raw = raw_stages
        self.log(f'Found {len(raw)} G/U stages')
        g_count = sum(1 for row in raw if str(row.get('entity_type', '')).upper() == 'G')
        u_count = sum(1 for row in raw if str(row.get('entity_type', '')).upper() == 'U')
        self.log(f'Stage types: G={g_count}, U={u_count}')

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
                    'x_bitrix_entity_type': stage.entity_type,
                    'x_bitrix_entity_id': str(stage.entity_id),
                    'user_id': False,
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
                    if record.user_id:
                        record.write({'user_id': False})
                    project_odoo_id = False
                    if stage.entity_type == 'G':
                        project_odoo_id = project_mapping.get(str(stage.entity_id))
                    elif stage.entity_type == 'U':
                        project_odoo_id = self._get_or_create_personal_project(stage.entity_id)
                    if project_odoo_id:
                        try:
                            record.write({'project_ids': [(4, project_odoo_id)]})
                        except Exception as e:
                            self.log(f'Warning: could not link stage {stage.id} to project {stage.entity_id}: {e}')

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=last_stage_id)

        self.log_stats()
