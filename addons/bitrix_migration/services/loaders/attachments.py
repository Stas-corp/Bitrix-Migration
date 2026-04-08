import base64
import logging

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

    def _get_sftp(self):
        if self._sftp is not None:
            return self._sftp
        try:
            import paramiko
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
            self._sftp.close()
            self._sftp = None

    def _download_file(self, file_path):
        """Download file from SFTP, return base64-encoded content."""
        sftp = self._get_sftp()
        full_path = self.sftp_base_path + file_path
        try:
            with sftp.open(full_path, 'rb') as f:
                data = f.read()
            return base64.b64encode(data)
        except FileNotFoundError:
            return None
        except Exception as e:
            self.log(f'SFTP download error for {full_path}: {e}')
            return None

    def run(self, attachment_type='task', raw_attachments=None):
        """Load attachments.

        Args:
            attachment_type: 'task' or 'comment'
            raw_attachments: optional pre-fetched list
        """
        if raw_attachments is None:
            self.log(f'Extracting Bitrix {attachment_type} attachments...')
            if attachment_type == 'task':
                raw_attachments = self.extractor.get_task_attachments()
            elif attachment_type == 'comment':
                raw_attachments = self.extractor.get_comment_attachments()
            else:
                self.log(f'Unknown attachment_type: {attachment_type}')
                return

        self.log(f'Found {len(raw_attachments)} {attachment_type} attachments')

        task_map = self.get_mapping().get_all_mappings('task')

        # Build message_id lookup for comment attachments
        message_map = {}
        if attachment_type == 'comment':
            msg_recs = self.env['mail.message'].sudo().search_read(
                [('x_bitrix_message_id', '!=', False)],
                ['id', 'x_bitrix_message_id'],
            )
            for rec in msg_recs:
                if rec['x_bitrix_message_id']:
                    message_map[str(rec['x_bitrix_message_id'])] = rec['id']

        # Use compound key for idempotency: entity_type:entity_id:file_path
        existing_att_mappings = self.get_mapping().get_all_mappings('attachment')

        processed = 0
        files_downloaded = 0

        try:
            for batch in self._batched(raw_attachments, self.batch_size):
                for row in batch:
                    att = BitrixAttachment(
                        entity_type=attachment_type,
                        entity_id=row.get('task_external_id', 0),
                        forum_message_id=row.get('forum_message_id'),
                        file_name=row.get('file_name', ''),
                        file_size=row.get('file_size', 0),
                        content_type=row.get('content_type', 'application/octet-stream'),
                        file_path=row.get('file_path', ''),
                        attached_at=row.get('attached_at'),
                    )

                    # Compound uniqueness key: type:entity_id:file_path
                    compound_key = f'{attachment_type}:{att.entity_id}:{att.file_path}'
                    if compound_key in existing_att_mappings or att.file_path in existing_att_mappings:
                        self.skipped_count += 1
                        processed += 1
                        continue

                    # Resolve parent
                    if attachment_type == 'task':
                        res_model = 'project.task'
                        res_id = task_map.get(str(att.entity_id))
                    elif attachment_type == 'comment':
                        # Link to mail.message if we can find it
                        msg_id = message_map.get(str(att.forum_message_id)) if att.forum_message_id else None
                        if msg_id:
                            res_model = 'mail.message'
                            res_id = msg_id
                        else:
                            # Fallback: link to task
                            res_model = 'project.task'
                            res_id = task_map.get(str(att.entity_id))
                    else:
                        res_model = None
                        res_id = None

                    if not res_id:
                        self.error_count += 1
                        self.errors.append((att.file_path, f'Parent entity not found: {att.entity_id}'))
                        processed += 1
                        continue

                    if not self.dry_run:
                        # Download file
                        file_data = self._download_file(att.file_path)
                        if file_data is None:
                            self.error_count += 1
                            self.errors.append((att.file_path, 'File not found on SFTP'))
                            processed += 1
                            continue

                        files_downloaded += 1

                        try:
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
                        except Exception as e:
                            self.error_count += 1
                            self.errors.append((att.file_path, str(e)))
                    else:
                        self.created_count += 1

                    processed += 1
                    if files_downloaded % 100 == 0 and files_downloaded > 0:
                        self.log(f'Downloaded {files_downloaded} files...')

                self.commit_checkpoint(processed)

        finally:
            self._close_sftp()

        self.log(f'Total files downloaded: {files_downloaded}')
        self.log_stats()
