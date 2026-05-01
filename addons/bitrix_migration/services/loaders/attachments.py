import base64
import logging
import time

from ..normalizers.dto import BitrixAttachment
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class AttachmentLoader(BaseLoader):
    """Loads Bitrix file attachments into ir.attachment via SFTP."""

    entity_type = 'attachment'
    batch_size = 200

    def __init__(self, env, extractor, batch_size=None, dry_run=False, log_callback=None,
                 sftp_host=None, sftp_port=22, sftp_user=None,
                 sftp_key_path=None, sftp_base_path='/home/bitrix/www'):
        super().__init__(env, extractor, batch_size, dry_run, log_callback)
        self.sftp_host = sftp_host
        self.sftp_port = sftp_port
        self.sftp_user = sftp_user
        self.sftp_key_path = sftp_key_path
        self.sftp_base_path = sftp_base_path.rstrip('/')
        self._sftp = None

    def _format_attachment_ref(self, attachment_type, att, compound_key=None):
        parts = [
            f'type={attachment_type}',
            f'entity={att.entity_id}',
            f'name={att.file_name}',
            f'path={att.file_path}',
        ]
        if att.forum_message_id:
            parts.append(f'forum_message={att.forum_message_id}')
        if att.file_size:
            parts.append(f'expected_size={att.file_size}')
        if compound_key:
            parts.append(f'key={compound_key}')
        return ', '.join(parts)

    def _make_compound_key(self, attachment_type, att):
        if attachment_type == 'comment' and att.forum_message_id:
            return f'comment:{att.entity_id}:{att.forum_message_id}:{att.file_path}'
        if attachment_type == 'meeting_comment' and att.forum_message_id:
            return f'meeting_comment:{att.entity_id}:{att.forum_message_id}:{att.file_path}'
        return f'{attachment_type}:{att.entity_id}:{att.file_path}'

    def _resolve_parent(self, attachment_type, att, task_map, message_map, meeting_map):
        if attachment_type == 'task':
            return 'project.task', task_map.get(str(att.entity_id))
        if attachment_type == 'comment':
            msg_id = message_map.get(str(att.forum_message_id)) if att.forum_message_id else None
            if msg_id:
                return 'mail.message', msg_id
            return 'project.task', task_map.get(str(att.entity_id))
        if attachment_type == 'meeting':
            return 'calendar.event', meeting_map.get(str(att.entity_id))
        if attachment_type == 'meeting_comment':
            msg_id = message_map.get(str(att.forum_message_id)) if att.forum_message_id else None
            if msg_id:
                return 'mail.message', msg_id
            return 'calendar.event', meeting_map.get(str(att.entity_id))
        return None, None

    def _get_sftp(self):
        if self._sftp is not None:
            return self._sftp
        try:
            import paramiko
            self.log(
                f'SFTP connecting to {self.sftp_user}@{self.sftp_host}:'
                f'{self.sftp_port} base_path={self.sftp_base_path}'
            )
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kwargs = {
                'hostname': self.sftp_host,
                'port': self.sftp_port,
                'username': self.sftp_user,
            }
            if self.sftp_key_path:
                connect_kwargs['key_filename'] = self.sftp_key_path
            ssh.connect(**connect_kwargs)
            self._sftp = ssh.open_sftp()
            self.log(f'SFTP connected to {self.sftp_host}')
            return self._sftp
        except Exception as e:
            self.log(f'SFTP connection failed: {e}')
            raise

    def _close_sftp(self):
        if self._sftp:
            self.log('SFTP closing connection')
            self._sftp.close()
            self._sftp = None

    def _download_file(self, file_path, expected_size=None):
        """Download file from SFTP, return base64-encoded content."""
        sftp = self._get_sftp()
        full_path = self.sftp_base_path + file_path
        started_at = time.monotonic()
        self.log(f'SFTP download start: {full_path} expected_size={expected_size or 0}')
        try:
            self.log(f'SFTP open start: {full_path}')
            with sftp.open(full_path, 'rb') as f:
                self.log(f'SFTP read start: {full_path}')
                data = f.read()
            elapsed = time.monotonic() - started_at
            self.log(
                f'SFTP download done: {full_path} bytes={len(data)} '
                f'duration={elapsed:.2f}s'
            )
            return base64.b64encode(data)
        except FileNotFoundError:
            elapsed = time.monotonic() - started_at
            self.log(f'SFTP file not found: {full_path} duration={elapsed:.2f}s')
            return None
        except Exception as e:
            elapsed = time.monotonic() - started_at
            self.log(f'SFTP download error for {full_path}: {e} duration={elapsed:.2f}s')
            return None

    def run(self, attachment_type='task', raw_attachments=None):
        """Load attachments.

        Args:
            attachment_type: 'task', 'comment', 'meeting', or 'meeting_comment'
            raw_attachments: optional pre-fetched list
        """
        if raw_attachments is None:
            self.log(f'Extracting Bitrix {attachment_type} attachments...')
            if attachment_type == 'task':
                raw_attachments = self.extractor.get_task_attachments()
            elif attachment_type == 'comment':
                raw_attachments = self.extractor.get_comment_attachments()
            elif attachment_type == 'meeting':
                raw_attachments = self.extractor.get_meeting_attachments()
            elif attachment_type == 'meeting_comment':
                raw_attachments = self.extractor.get_meeting_comment_attachments()
            else:
                self.log(f'Unknown attachment_type: {attachment_type}')
                return

        self.log(f'Found {len(raw_attachments)} {attachment_type} attachments')

        task_map = self.get_mapping().get_all_mappings('task')
        self.log(f'Loaded task mapping entries: {len(task_map)}')

        meeting_map = {}
        if attachment_type in ('meeting', 'meeting_comment'):
            meeting_map = self.get_mapping().get_all_mappings('meeting')
            self.log(f'Loaded meeting mapping entries: {len(meeting_map)}')

        # Build message_id lookup for comment attachments
        message_map = {}
        if attachment_type in ('comment', 'meeting_comment'):
            msg_recs = self.env['mail.message'].sudo().search_read(
                [('x_bitrix_message_id', '!=', False)],
                ['id', 'x_bitrix_message_id'],
            )
            for rec in msg_recs:
                if rec['x_bitrix_message_id']:
                    message_map[str(rec['x_bitrix_message_id'])] = rec['id']
            self.log(f'Loaded comment message mapping entries: {len(message_map)}')
            if not message_map:
                self.log(
                    f'WARN: {attachment_type} attachments requested but message_map is empty — '
                    f'all attachments will fall back to parent entity. '
                    f'Did CommentLoader run first?'
                )

        # Use compound key for idempotency: entity_type:entity_id:file_path
        existing_att_mappings = self.get_mapping().get_all_mappings('attachment')
        self.log(f'Loaded existing attachment mapping entries: {len(existing_att_mappings)}')

        processed = 0
        files_downloaded = 0
        batch_no = 0

        try:
            for batch in self._batched(raw_attachments, self.batch_size):
                batch_no += 1
                self.log(
                    f'Batch {batch_no} start: size={len(batch)}, '
                    f'processed_before={processed}'
                )
                for row in batch:
                    att = BitrixAttachment(
                        entity_type=attachment_type,
                        entity_id=row.get('task_external_id') or row.get('meeting_external_id', 0),
                        forum_message_id=row.get('forum_message_id'),
                        file_name=row.get('file_name', ''),
                        file_size=row.get('file_size', 0),
                        content_type=row.get('content_type', 'application/octet-stream'),
                        file_path=row.get('file_path', ''),
                        attached_at=row.get('attached_at'),
                    )

                    # Compound uniqueness key
                    compound_key = self._make_compound_key(attachment_type, att)
                    att_ref = self._format_attachment_ref(attachment_type, att, compound_key)
                    self.log(f'Attachment start: {att_ref}')
                    # Also check legacy plain file_path key for backward-safe skip
                    legacy_task_key = f'task:{att.entity_id}:{att.file_path}'
                    legacy_comment_key = f'comment:{att.entity_id}:{att.file_path}'
                    legacy_meeting_key = f'meeting:{att.entity_id}:{att.file_path}'
                    legacy_meeting_comment_key = f'meeting_comment:{att.entity_id}:{att.file_path}'
                    existing_key = None
                    for key in (
                        compound_key,
                        att.file_path,
                        legacy_task_key,
                        legacy_comment_key,
                        legacy_meeting_key,
                        legacy_meeting_comment_key,
                    ):
                        if key in existing_att_mappings:
                            existing_key = key
                            break
                    if existing_key:
                        self.skipped_count += 1
                        processed += 1
                        self.log(
                            f'Attachment skip existing mapping: matched_key={existing_key} '
                            f'processed={processed}'
                        )
                        continue

                    # Resolve parent
                    res_model, res_id = self._resolve_parent(
                        attachment_type, att, task_map, message_map, meeting_map,
                    )

                    if not res_id:
                        self.error_count += 1
                        self.errors.append((att.file_path, f'Parent entity not found: {att.entity_id}'))
                        processed += 1
                        self.log(
                            f'Attachment parent missing: {att_ref}, processed={processed}, '
                            f'errors={self.error_count}'
                        )
                        continue
                    self.log(
                        f'Attachment parent resolved: {att_ref}, '
                        f'res_model={res_model}, res_id={res_id}'
                    )

                    if not self.dry_run:
                        # Download file
                        file_data = self._download_file(att.file_path, att.file_size)
                        if file_data is None:
                            self.error_count += 1
                            self.errors.append((att.file_path, 'File not found on SFTP'))
                            processed += 1
                            self.log(
                                f'Attachment download failed: {att_ref}, processed={processed}, '
                                f'errors={self.error_count}'
                            )
                            continue

                        files_downloaded += 1

                        try:
                            self.log(
                                f'Creating ir.attachment: {att_ref}, '
                                f'res_model={res_model}, res_id={res_id}'
                            )
                            ir_att = self.env['ir.attachment'].sudo().create({
                                'name': att.file_name,
                                'datas': file_data,
                                'res_model': res_model,
                                'res_id': res_id,
                                'mimetype': att.content_type,
                            })
                            self.get_mapping().set_mapping(
                                compound_key, 'attachment', 'ir.attachment', ir_att.id,
                            )
                            self.created_count += 1
                            self.log(
                                f'Attachment created: ir_attachment_id={ir_att.id}, '
                                f'created={self.created_count}, downloaded={files_downloaded}'
                            )
                        except Exception as e:
                            self.error_count += 1
                            self.errors.append((att.file_path, str(e)))
                            self.log(
                                f'Attachment create failed: {att_ref}, error={e}, '
                                f'errors={self.error_count}'
                            )
                    else:
                        self.created_count += 1
                        self.log(f'DRY RUN attachment would be created: {att_ref}')

                    processed += 1
                    if files_downloaded % 100 == 0 and files_downloaded > 0:
                        self.log(f'Downloaded {files_downloaded} files...')

                self.log(
                    f'Batch {batch_no} done: processed={processed}, '
                    f'created={self.created_count}, skipped={self.skipped_count}, '
                    f'errors={self.error_count}'
                )
                self.commit_checkpoint(processed)

        finally:
            self._close_sftp()

        self.log(f'Total files downloaded: {files_downloaded}')
        self.log_stats()
