import logging

from ..normalizers.dto import BitrixComment
from .base import BaseLoader

_logger = logging.getLogger(__name__)

MIGRATION_BOT_NAME = 'Bitrix Migration Bot'


class CommentLoader(BaseLoader):
    """Loads Bitrix comments into mail.message (chatter) on project.task.

    Supports two authorship modes (both can be active simultaneously):
      - preserve_authorship: try to resolve Bitrix AUTHOR_ID → Odoo partner
      - fallback_system_author: use a system "Migration Bot" partner if author not found,
        and store original Bitrix author ID in x_bitrix_author_id

    Idempotency: uses x_bitrix_message_id on mail.message to skip duplicates.
    Direct ORM create (not message_post) to avoid notifications and tracking.
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

        partner = self.env['res.partner'].sudo().search(
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

    def run(self, raw_comments=None):
        """Load comments. Optionally accepts pre-fetched raw_comments."""
        system_partner_id = self._ensure_system_author()

        if raw_comments is None:
            self.log('Extracting Bitrix comments (real only)...')
            raw_comments = self.extractor.get_comments()
        self.log(f'Found {len(raw_comments)} comments to process')

        # Pre-load mappings
        task_map = self.get_mapping().get_all_mappings('task')
        user_map = self.get_mapping().get_all_mappings('user')

        # Pre-load existing x_bitrix_message_id for idempotency
        existing_msg_ids = set()
        existing_recs = self.env['mail.message'].sudo().search_read(
            [('x_bitrix_message_id', '!=', False)],
            ['x_bitrix_message_id'],
        )
        for r in existing_recs:
            if r['x_bitrix_message_id']:
                existing_msg_ids.add(r['x_bitrix_message_id'])
        self.log(f'Already migrated: {len(existing_msg_ids)} comments')

        # Get subtype for notes
        note_subtype = self.env.ref('mail.mt_note', raise_if_not_found=False)
        note_subtype_id = note_subtype.id if note_subtype else False

        processed = 0
        authors_resolved = 0
        authors_fallback = 0

        for batch in self._batched(raw_comments, self.batch_size):
            for row in batch:
                comment = BitrixComment(**row)
                msg_id_str = str(comment.message_id)

                # Idempotency check
                if msg_id_str in existing_msg_ids:
                    self.skipped_count += 1
                    processed += 1
                    continue

                # Resolve task
                odoo_task_id = task_map.get(str(comment.entity_id))
                if not odoo_task_id:
                    self.error_count += 1
                    self.errors.append((
                        msg_id_str,
                        f'Task bitrix_id={comment.entity_id} not found in mapping',
                    ))
                    processed += 1
                    continue

                # ── Author resolution (CRITICAL) ──────────────────────
                author_id = system_partner_id
                x_bitrix_author_id = None

                if self.preserve_authorship and comment.author_bitrix_id:
                    resolved_partner = user_map.get(str(comment.author_bitrix_id))
                    if resolved_partner:
                        author_id = resolved_partner
                        authors_resolved += 1
                    else:
                        # Fallback: use system author, preserve original ID
                        x_bitrix_author_id = str(comment.author_bitrix_id)
                        authors_fallback += 1
                elif comment.author_bitrix_id:
                    x_bitrix_author_id = str(comment.author_bitrix_id)
                    authors_fallback += 1

                # ── Create mail.message ───────────────────────────────
                if not self.dry_run:
                    vals = {
                        'model': 'project.task',
                        'res_id': odoo_task_id,
                        'body': comment.body or '',
                        'message_type': 'comment',
                        'author_id': author_id,
                        'x_bitrix_message_id': msg_id_str,
                    }
                    if note_subtype_id:
                        vals['subtype_id'] = note_subtype_id
                    if comment.date:
                        vals['date'] = comment.date
                    if x_bitrix_author_id:
                        vals['x_bitrix_author_id'] = x_bitrix_author_id

                    try:
                        self.env['mail.message'].sudo().with_context(
                            mail_create_nolog=True,
                            mail_create_nosubscribe=True,
                        ).create(vals)
                        self.created_count += 1
                        existing_msg_ids.add(msg_id_str)
                    except Exception as e:
                        self.error_count += 1
                        self.errors.append((msg_id_str, str(e)))
                else:
                    self.created_count += 1

                processed += 1

            self.commit_checkpoint(processed, last_bitrix_id=comment.message_id)

        self.log(f'Authors: resolved={authors_resolved}, fallback={authors_fallback}')
        self.log_stats()
