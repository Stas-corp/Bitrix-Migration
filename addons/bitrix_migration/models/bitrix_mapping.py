from odoo import models, fields, api


class BitrixMigrationMapping(models.Model):
    _name = 'bitrix.migration.mapping'
    _description = 'Bitrix to Odoo ID Mapping'
    _rec_name = 'bitrix_id'

    bitrix_id = fields.Char(required=True, index=True, string='Bitrix ID')
    odoo_model = fields.Char(required=True, string='Odoo Model')
    odoo_id = fields.Integer(required=True, string='Odoo Record ID')
    entity_type = fields.Selection([
        ('project', 'Project'),
        ('task', 'Task'),
        ('stage', 'Stage'),
        ('tag', 'Tag'),
        ('comment', 'Comment'),
        ('attachment', 'Attachment'),
        ('user', 'User'),
        ('meeting', 'Meeting'),
        ('department', 'Department'),
        ('employee', 'Employee'),
    ], required=True, string='Entity Type')
    extra_data = fields.Text(string='Extra Data (JSON)')

    _sql_constraints = [
        ('bitrix_entity_unique', 'UNIQUE(bitrix_id, entity_type)',
         'Mapping must be unique per Bitrix ID and entity type.'),
    ]

    def get_odoo_id(self, bitrix_id, entity_type):
        rec = self.search([
            ('bitrix_id', '=', str(bitrix_id)),
            ('entity_type', '=', entity_type),
        ], limit=1)
        return rec.odoo_id if rec else False

    def set_mapping(self, bitrix_id, entity_type, odoo_model, odoo_id):
        existing = self.search([
            ('bitrix_id', '=', str(bitrix_id)),
            ('entity_type', '=', entity_type),
        ], limit=1)
        if existing:
            if existing.odoo_id != odoo_id:
                existing.write({'odoo_id': odoo_id, 'odoo_model': odoo_model})
            return existing
        return self.create({
            'bitrix_id': str(bitrix_id),
            'entity_type': entity_type,
            'odoo_model': odoo_model,
            'odoo_id': odoo_id,
        })

    def get_all_mappings(self, entity_type):
        recs = self.search([('entity_type', '=', entity_type)])
        return {r.bitrix_id: r.odoo_id for r in recs}
