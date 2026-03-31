import logging

from ..normalizers.dto import BitrixComment
from .base import BaseLoader

_logger = logging.getLogger(__name__)

MIGRATION_BOT_NAME = 'Bitrix Migration Bot'


class CommentLoader(BaseLoader):
    """Loads Bitrix comments into mail.message (chatter) on project.task.

    Comments in Odoo always use res.partner as the visible author, but we also
    preserve the matched hr.employee so history can be re-linked later if the
    employee gets an Odoo user after the initial import.
    """

    entity_type = 'comment'
    batch_size = 2000

    def __init__(self, env, extractor, batch_size=None, dry_run=False, log_callback=None,
                 preserve_authorship=True, fallback_system_author=True,
                 system_author_partner_id=None):
        super().__init__(env, extractor, batch_size, dry_run, log_callback)
        self.preserve_authorship = preserve_authorship
        self.fallback_system_author = fallback_system_author
        self.system_author_partner_id = system_author_partner_id

    def _ensure_system_author(self):
        """Get or create the system migration bot partner."""
        if self.system_author_partner_id:
            return self.system_author_partner_id

        partner = self.env['res.partner'].sudo().with_context(active_test=False).search(
            [('name', '=', MIGRATION_BOT_NAME)], limit=1,
        )
        if not partner:
            partner = self.env['res.partner'].sudo().create({
                'name': MIGRATION_BOT_NAME,
                'email': 'migration-bot@bitrix.local',
                'active': False,
            })
            self.env.cr.commit()
            self.log(f'Created migration bot partner: id={partner.id}')

        self.system_author_partner_id = partner.id
        return partner.id

    def _resolve_comment_author(self, comment, system_partner_id, user_map, employee_map):
        """Resolve author preferring employee-linked contacts over user mapping."""
        author_id = system_partner_id
        x_bitrix_author_id = None
        employee = self.find_employee_by_bitrix_id(
            comment.author_bitrix_id, employee_map=employee_map,
        )

        if self.preserve_authorship and comment.author_bitrix_id:
            partner = self.get_partner_from_employee(employee)
            resolved_partner_id = partner.id if partner else user_map.get(str(comment.author_bitrix_id))
            if resolved_partner_id:
                author_id = resolved_partner_id
            else:
                x_bitrix_author_id = str(comment.author_bitrix_id)
        elif comment.author_bitrix_id:
            x_bitrix_author_id = str(comment.author_bitrix_id)

        return {
            'author_id': author_id,
            'employee_id': employee.id if employee else False,
            'x_bitrix_author_id': x_bitrix_author_id,
        }

    def _update_existing_message(self, message, author_data, can_store_employee_author):
        """Backfill employee and author links on previously imported comments."""
        vals = {}

        if can_store_employee_author and (
            author_data['employee_id']
            and message.x_bitrix_author_employee_id.id != author_data['employee_id']
        ):
            vals['x_bitrix_author_employee_id'] = author_data['employee_id']

        if author_data['x_bitrix_author_id']:
            if message.x_bitrix_author_id != author_data['x_bitrix_author_id']:
                vals['x_bitrix_author_id'] = author_data['x_bitrix_author_id']
        elif (
            author_data['author_id']
            and message.author_id.id != author_data['author_id']
            and message.x_bitrix_author_id
        ):
            vals['author_id'] = author_data['author_id']
            vals['x_bitrix_author_id'] = False

        if vals:
            message.write(vals)
            self.updated_count += 1

    def run(self, raw_comments=None):
        """Load comments. Optionally accepts pre-fetched raw_comments."""
        system_partner_id = self._ensure_system_author()

        if raw_comments is None:
            self.log('Extracting Bitrix comments (real only)...')
            raw_comments = self.extractor.get_comments()
        self.log(f'Found {len(raw_comments)} comments to process')

        task_map = self.get_mapping().get_all_mappings('task')
        user_map = self.get_mapping().get_all_mappings('user')
        employee_map = self.get_mapping().get_all_mappings('employee')
        can_store_employee_author = self.db_column_exists(
            'mail_message', 'x_bitrix_author_employee_id',
        )
        if not can_store_employee_author:
            self.log_once(
                'missing_mail_message_x_bitrix_author_employee_id',
                'Skipping comment-to-employee links: column '
                '"mail_message.x_bitrix_author_employee_id" is missing. '
                'Upgrade the bitrix_migration module to enable employee-based history.',
            )

        existing_msg_map = {}
        existing_recs = self.env['mail.message'].sudo().search_read(
            [('x_bitrix_message_id', '!=', False)],
            ['id', 'x_bitrix_message_id'],
        )
        for rec in existing_recs:
            if rec['x_bitrix_message_id']:
                existing_msg_map[rec['x_bitrix_message_id']] = rec['id']
        self.log(f'Already migrated: {len(existing_msg_map)} comments')

        note_subtype = self.env.ref('mail.mt_note', raise_if_not_found=False)
        note_subtype_id = note_subtype.id if note_subtype else False

        processed = 0
        authors_resolved = 0
        authors_fallback = 0

        for batch in self._batched(raw_comments, self.batch_size):
            last_message_id = None

            for row in batch:
                comment = BitrixComment(**row)
                msg_id_str = str(comment.message_id)
                last_message_id = comment.message_id

                odoo_task_id = task_map.get(str(comment.entity_id))
                if not odoo_task_id:
                    self.error_count += 1
                    self.errors.append((
                        msg_id_str,
                        f'Task bitrix_id={comment.entity_id} not found in mapping',
                    ))
                    processed += 1
                    continue

                author_data = self._resolve_comment_author(
                    comment, system_partner_id, user_map, employee_map,
                )
                if comment.author_bitrix_id:
                    if author_data['x_bitrix_author_id']:
                        authors_fallback += 1
                    else:
                        authors_resolved += 1

                existing_message_id = existing_msg_map.get(msg_id_str)
                if existing_message_id:
                    if not self.dry_run:
                        message = self.env['mail.message'].sudo().browse(existing_message_id).exists()
                        if message:
                            self._update_existing_message(
                                message, author_data, can_store_employee_author,
                            )
                    self.skipped_count += 1
                    processed += 1
                    continue

                if not self.dry_run:
                    vals = {
                        'model': 'project.task',
                        'res_id': odoo_task_id,
                        'body': comment.body or '',
                        'message_type': 'comment',
                        'author_id': author_data['author_id'] or system_partner_id,
                        'x_bitrix_message_id': msg_id_str,
                    }
                    if note_subtype_id:
                        vals['subtype_id'] = note_subtype_id
                    if comment.date:
                        vals['date'] = comment.date
                    if can_store_employee_author and author_data['employee_id']:
                        vals['x_bitrix_author_employee_id'] = author_data['employee_id']
                    if author_data['x_bitrix_author_id']:
                        vals['x_bitrix_author_id'] = author_data['x_bitrix_author_id']

                    try:
                        message = self.env['mail.message'].sudo().with_context(
                            mail_create_nolog=True,
                            mail_create_nosubscribe=True,
                        ).create(vals)
                        self.created_count += 1
                        existing_msg_map[msg_id_str] = message.id
                    except Exception as e:
                        self.error_count += 1
                        self.errors.append((msg_id_str, str(e)))
                else:
                    self.created_count += 1

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=last_message_id)

        self.log(f'Authors: resolved={authors_resolved}, fallback={authors_fallback}')
        self.log_stats()
