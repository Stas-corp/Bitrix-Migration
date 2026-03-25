import logging

from .base import BaseLoader

_logger = logging.getLogger(__name__)


class UserLoader(BaseLoader):
    """Maps Bitrix users to Odoo res.partner via hr.employee.work_contact_id.

    Does NOT create users — only builds the mapping table.
    Search order:
      1. res.users (login/email) → user.partner_id
      2. hr.employee (work_email) → employee.work_contact_id → res.partner
      3. res.partner (email) — direct fallback
    """

    entity_type = 'user'
    batch_size = 100

    def run(self):
        self.log('Extracting Bitrix users...')
        bitrix_users = self.extractor.get_users()
        self.log(f'Found {len(bitrix_users)} Bitrix users')

        mapping = self.get_mapping()
        existing_mappings = mapping.get_all_mappings('user')

        processed = 0
        for batch in self._batched(bitrix_users, self.batch_size):
            for bu in batch:
                bitrix_id = str(bu['ID'])

                if bitrix_id in existing_mappings:
                    self.skipped_count += 1
                    continue

                email = (bu.get('EMAIL') or '').strip().lower()
                login = (bu.get('LOGIN') or '').strip().lower()
                partner_id = None

                # 1. Try res.users by login or email
                if login or email:
                    domain = []
                    if login:
                        domain = [('login', '=ilike', login)]
                    elif email:
                        domain = [('login', '=ilike', email)]
                    user = self.env['res.users'].sudo().search(domain, limit=1)
                    if not user and email and login != email:
                        user = self.env['res.users'].sudo().search(
                            [('login', '=ilike', email)], limit=1,
                        )
                    if user:
                        partner_id = user.partner_id.id

                # 2. Try hr.employee by work_email → work_contact_id
                if not partner_id and email:
                    emp = self.env['hr.employee'].sudo().search(
                        [('work_email', '=ilike', email)], limit=1,
                    )
                    if emp and emp.work_contact_id:
                        partner_id = emp.work_contact_id.id

                # 3. Direct res.partner fallback
                if not partner_id and email:
                    partner = self.env['res.partner'].sudo().search(
                        [('email', '=ilike', email)], limit=1,
                    )
                    if partner:
                        partner_id = partner.id

                if partner_id:
                    if not self.dry_run:
                        mapping.set_mapping(bitrix_id, 'user', 'res.partner', partner_id)
                    self.created_count += 1
                else:
                    bname = f"{bu.get('NAME', '')} {bu.get('LAST_NAME', '')}".strip()
                    self.error_count += 1
                    self.errors.append((bitrix_id, f'No Odoo partner found for {bname} ({email})'))

                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()
        return self.get_mapping().get_all_mappings('user')

    def get_partner_id(self, bitrix_user_id):
        """Convenience: returns Odoo partner id for a Bitrix user, or None."""
        odoo_id = self.get_mapping().get_odoo_id(str(bitrix_user_id), 'user')
        return odoo_id if odoo_id else None
