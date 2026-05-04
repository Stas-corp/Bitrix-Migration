import base64
import hashlib
from html import escape
import logging
import os
import re
import socket
import time

from ..normalizers.dto import BitrixAttachment
from .base import BaseLoader

_logger = logging.getLogger(__name__)


class AttachmentLoader(BaseLoader):
    """Loads Bitrix file attachments into ir.attachment via SFTP."""

    entity_type = 'attachment'
    batch_size = 200
    resumable_chunk_size = 1 * 1024 * 1024
    resumable_deadline_margin = 3.0
    resumable_sftp_timeout = 20.0
    resumable_connection_max_seconds = 110.0

    def __init__(self, env, extractor, batch_size=None, dry_run=False, log_callback=None,
                 sftp_host=None, sftp_port=22, sftp_user=None,
                 sftp_key_path=None, sftp_base_path='/home/bitrix/www',
                 progress_callback=None):
        super().__init__(env, extractor, batch_size, dry_run, log_callback)
        self.sftp_host = sftp_host
        self.sftp_port = sftp_port
        self.sftp_user = sftp_user
        self.sftp_key_path = sftp_key_path
        self.sftp_base_path = sftp_base_path.rstrip('/')
        self.progress_callback = progress_callback
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
            self._sftp.get_channel().settimeout(self.resumable_sftp_timeout)
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

    def _get_raw_attachments(self, attachment_type):
        if attachment_type == 'task':
            return self.extractor.get_task_attachments()
        if attachment_type == 'comment':
            return self.extractor.get_comment_attachments()
        if attachment_type == 'meeting':
            return self.extractor.get_meeting_attachments()
        if attachment_type == 'meeting_comment':
            return self.extractor.get_meeting_comment_attachments()
        self.log(f'Unknown attachment_type: {attachment_type}')
        return []

    def _build_attachment_context(self, attachment_type):
        task_map = self.get_mapping().get_all_mappings('task')
        self.log(f'Loaded task mapping entries: {len(task_map)}')

        meeting_map = {}
        if attachment_type in ('meeting', 'meeting_comment'):
            meeting_map = self.get_mapping().get_all_mappings('meeting')
            self.log(f'Loaded meeting mapping entries: {len(meeting_map)}')

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

        return task_map, message_map, meeting_map

    def _row_to_attachment(self, attachment_type, row):
        return BitrixAttachment(
            entity_type=attachment_type,
            entity_id=row.get('task_external_id') or row.get('meeting_external_id', 0),
            forum_message_id=row.get('forum_message_id'),
            disk_file_id=row.get('disk_file_id', ''),
            disk_attached_object_id=row.get('disk_attached_object_id', ''),
            file_name=row.get('file_name', ''),
            file_size=row.get('file_size', 0),
            content_type=row.get('content_type', 'application/octet-stream'),
            file_path=row.get('file_path', ''),
            attached_at=row.get('attached_at'),
        )

    def _find_existing_attachment_key(self, compound_key, attachment_type, att, existing_att_mappings):
        legacy_task_key = f'task:{att.entity_id}:{att.file_path}'
        legacy_comment_key = f'comment:{att.entity_id}:{att.file_path}'
        legacy_meeting_key = f'meeting:{att.entity_id}:{att.file_path}'
        legacy_meeting_comment_key = f'meeting_comment:{att.entity_id}:{att.file_path}'
        for key in (
            compound_key,
            att.file_path,
            legacy_task_key,
            legacy_comment_key,
            legacy_meeting_key,
            legacy_meeting_comment_key,
        ):
            if key in existing_att_mappings:
                return key
        return None

    def _ensure_task_description_attachment_link(self, ir_att, target_model, target_id, att):
        if att.entity_type != 'task' or target_model != 'project.task' or not target_id:
            return False

        task = self.env['project.task'].sudo().with_context(active_test=False).browse(target_id).exists()
        if not task:
            return False

        url = f'/web/content/{ir_att.id}?download=true'
        description = task.description or ''

        file_name = escape(ir_att.name or att.file_name or 'Attachment')
        link_html = f'<a href="{url}" target="_blank" rel="noopener">{file_name}</a>'
        replacement = f'<span class="o_bitrix_description_attachment">{link_html}</span>'
        updated = description

        disk_file_ids = []
        if att.disk_file_id:
            disk_file_ids.append(str(att.disk_file_id))
            if str(att.disk_file_id).startswith('n') and str(att.disk_file_id)[1:].isdigit():
                disk_file_ids.append(str(att.disk_file_id)[1:])
        if att.disk_attached_object_id:
            disk_file_ids.append(str(att.disk_attached_object_id))

        disk_file_ids = list(dict.fromkeys(disk_file_ids))
        replaced_marker = False
        for disk_file_id in disk_file_ids:
            raw_pattern = re.compile(
                r'\[DISK\s+FILE\s+ID=' + re.escape(disk_file_id) + r'\]',
                flags=re.IGNORECASE,
            )
            updated, count = raw_pattern.subn(replacement, updated)
            replaced_marker = replaced_marker or bool(count)

            placeholder_pattern = re.compile(
                r'<span\b(?=[^>]*\bclass="[^"]*\bo_bitrix_disk_file_placeholder\b[^"]*")'
                r'(?=[^>]*\bdata-bitrix-disk-file-id="' + re.escape(disk_file_id) + r'")'
                r'[^>]*>.*?</span>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            updated, count = placeholder_pattern.subn(replacement, updated)
            replaced_marker = replaced_marker or bool(count)

        if replaced_marker:
            # Remove the old appended fallback for this same attachment if it exists.
            appended_pattern = re.compile(
                r'<p\b(?=[^>]*\bclass="[^"]*\bo_bitrix_description_attachment\b[^"]*")'
                r'[^>]*>\s*<a\b(?=[^>]*\bhref="' + re.escape(url) + r'")'
                r'[^>]*>.*?</a>\s*</p>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            updated = appended_pattern.sub('', updated)
        elif url in updated:
            return False
        else:
            updated = (
                f'{updated}<p class="o_bitrix_description_attachment">'
                f'Attachment: {link_html}</p>'
            )

        if updated == description:
            return False

        task.write({'description': updated})
        action = 'replaced' if replaced_marker else 'added'
        self.log(
            f'Task description attachment link {action}: '
            f'task_id={task.id}, ir_attachment_id={ir_att.id}, '
            f'disk_file_id={att.disk_file_id or ""}'
        )
        return True

    def _attachment_disk_file_ids(self, att):
        disk_file_ids = []
        if att.disk_file_id:
            disk_file_ids.append(str(att.disk_file_id))
            if str(att.disk_file_id).startswith('n') and str(att.disk_file_id)[1:].isdigit():
                disk_file_ids.append(str(att.disk_file_id)[1:])
        if att.disk_attached_object_id:
            disk_file_ids.append(str(att.disk_attached_object_id))
        return list(dict.fromkeys(disk_file_ids))

    def _ensure_message_body_attachment_link(self, ir_att, message, att):
        if att.entity_type not in ('comment', 'meeting_comment') or not message:
            return False

        message = message.sudo().exists()
        if not message:
            return False

        url = f'/web/content/{ir_att.id}?download=true'
        body = message.body or ''
        if url in body:
            return False

        file_name = escape(ir_att.name or att.file_name or 'Attachment')
        link_html = f'<a href="{url}" target="_blank" rel="noopener">{file_name}</a>'
        replacement = f'<span class="o_bitrix_message_attachment">{link_html}</span>'
        updated = body
        replaced_marker = False

        for disk_file_id in self._attachment_disk_file_ids(att):
            raw_pattern = re.compile(
                r'\[DISK\s+FILE\s+ID=' + re.escape(disk_file_id) + r'\]',
                flags=re.IGNORECASE,
            )
            updated, count = raw_pattern.subn(replacement, updated, count=1)
            replaced_marker = replaced_marker or bool(count)
            if count:
                break

            placeholder_pattern = re.compile(
                r'<span\b(?=[^>]*\bclass="[^"]*\bo_bitrix_disk_file_placeholder\b[^"]*")'
                r'(?=[^>]*\bdata-bitrix-disk-file-id="' + re.escape(disk_file_id) + r'")'
                r'[^>]*>.*?</span>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            updated, count = placeholder_pattern.subn(replacement, updated, count=1)
            replaced_marker = replaced_marker or bool(count)
            if count:
                break

        if not replaced_marker:
            # mail.message sanitization may strip data-bitrix-disk-file-id, leaving only
            # a generic placeholder. Replace one placeholder per attachment.
            generic_placeholder_pattern = re.compile(
                r'<span\b(?=[^>]*\bclass="[^"]*\bo_bitrix_disk_file_placeholder\b[^"]*")'
                r'[^>]*>.*?</span>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            updated, count = generic_placeholder_pattern.subn(replacement, updated, count=1)
            replaced_marker = bool(count)

        if not replaced_marker:
            updated = f'{updated}<p class="o_bitrix_message_attachment">Attachment: {link_html}</p>'

        if updated == body:
            return False

        message.write({'body': updated})
        action = 'replaced' if replaced_marker else 'added'
        self.log(
            f'Message body attachment link {action}: '
            f'message_id={message.id}, ir_attachment_id={ir_att.id}, '
            f'disk_file_id={att.disk_file_id or ""}'
        )
        return True

    def _default_tmp_path(self, tmp_dir, compound_key, file_name):
        digest = hashlib.sha1(compound_key.encode('utf-8')).hexdigest()
        _, ext = os.path.splitext(file_name or '')
        safe_ext = ''.join(ch for ch in ext[:20] if ch.isalnum() or ch in '._-')
        return os.path.join(tmp_dir, f'{digest}{safe_ext}.part')

    def _deadline_reached(self, deadline):
        if deadline is None:
            return False
        return time.monotonic() >= deadline - self.resumable_deadline_margin

    def _connection_limit_reached(self, started_at):
        return time.monotonic() - started_at >= self.resumable_connection_max_seconds

    def _report_resumable_progress(self, progress):
        if not self.progress_callback:
            return
        try:
            self.progress_callback(progress)
        except Exception:
            _logger.warning('Could not update attachment resumable progress', exc_info=True)

    def _download_file_chunked(
        self, file_path, tmp_path, offset=0, expected_size=None, deadline=None,
        chunk_size=None, progress_callback=None,
    ):
        """Download a file incrementally into tmp_path.

        Returns (status, bytes_written), where status is one of:
        done, partial, missing, error.
        """
        chunk_size = chunk_size or self.resumable_chunk_size
        full_path = self.sftp_base_path + file_path
        started_at = time.monotonic()
        bytes_written = int(offset or 0)
        expected_size = int(expected_size or 0)
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
        if bytes_written and not os.path.exists(tmp_path):
            self.log(
                f'WARN: tmp file missing for resumed attachment; restarting download: '
                f'{tmp_path}, previous_offset={bytes_written}'
            )
            bytes_written = 0

        if os.path.exists(tmp_path):
            tmp_size = os.path.getsize(tmp_path)
            if tmp_size > bytes_written:
                bytes_written = tmp_size

        if expected_size and bytes_written >= expected_size and os.path.exists(tmp_path):
            self.log(
                f'SFTP chunked download already complete: {full_path} '
                f'bytes={bytes_written} expected_size={expected_size}'
            )
            return 'done', bytes_written

        sftp = self._get_sftp()
        self.log(
            f'SFTP chunked download start: {full_path} offset={bytes_written} '
            f'expected_size={expected_size}'
        )
        try:
            with sftp.open(full_path, 'rb') as remote:
                if bytes_written:
                    remote.seek(bytes_written)
                with open(tmp_path, 'ab') as local:
                    while True:
                        if self._connection_limit_reached(started_at):
                            elapsed = time.monotonic() - started_at
                            self.log(
                                f'SFTP chunked download paused by connection limit: '
                                f'{full_path} bytes={bytes_written} duration={elapsed:.2f}s'
                            )
                            return 'partial', bytes_written

                        if self._deadline_reached(deadline):
                            self.log(
                                f'SFTP chunked download paused: {full_path} '
                                f'bytes={bytes_written}'
                            )
                            return 'partial', bytes_written

                        read_size = chunk_size
                        if expected_size:
                            remaining = expected_size - bytes_written
                            if remaining <= 0:
                                elapsed = time.monotonic() - started_at
                                self.log(
                                    f'SFTP chunked download done: {full_path} '
                                    f'bytes={bytes_written} duration={elapsed:.2f}s'
                                )
                                return 'done', bytes_written
                            read_size = min(chunk_size, remaining)

                        chunk = remote.read(read_size)
                        if not chunk:
                            elapsed = time.monotonic() - started_at
                            self.log(
                                f'SFTP chunked download done: {full_path} '
                                f'bytes={bytes_written} duration={elapsed:.2f}s'
                            )
                            return 'done', bytes_written

                        local.write(chunk)
                        bytes_written += len(chunk)
                        if progress_callback:
                            progress_callback(bytes_written)

                        if expected_size and bytes_written >= expected_size:
                            elapsed = time.monotonic() - started_at
                            self.log(
                                f'SFTP chunked download done: {full_path} '
                                f'bytes={bytes_written} duration={elapsed:.2f}s'
                            )
                            return 'done', bytes_written

        except FileNotFoundError:
            elapsed = time.monotonic() - started_at
            self.log(f'SFTP file not found: {full_path} duration={elapsed:.2f}s')
            return 'missing', bytes_written
        except socket.timeout:
            elapsed = time.monotonic() - started_at
            self.log(
                f'SFTP chunked download timeout: {full_path} '
                f'bytes={bytes_written} duration={elapsed:.2f}s'
            )
            self._close_sftp()
            return 'partial', bytes_written
        except Exception as e:
            elapsed = time.monotonic() - started_at
            self.log(f'SFTP chunked download error for {full_path}: {e} duration={elapsed:.2f}s')
            return 'error', bytes_written

    def _create_attachment_from_tmp(self, att, tmp_path, res_model, res_id, compound_key):
        message = self.env['mail.message'].sudo()
        target_model = res_model
        target_id = res_id
        if res_model == 'mail.message':
            message = message.browse(res_id).exists()
            if message and message.model and message.res_id:
                target_model = message.model
                target_id = message.res_id

        self.log(
            f'Creating ir.attachment from tmp: '
            f'{self._format_attachment_ref(att.entity_type, att, compound_key)}, '
            f'res_model={target_model}, res_id={target_id}, tmp_path={tmp_path}'
        )
        with open(tmp_path, 'rb') as fp:
            raw_data = fp.read()

        ir_att = self.env['ir.attachment'].sudo().create({
            'name': att.file_name,
            'raw': raw_data,
            'res_model': target_model,
            'res_id': target_id,
            'mimetype': att.content_type,
        })
        if message:
            message.write({'attachment_ids': [(4, ir_att.id)]})
            self._ensure_message_body_attachment_link(ir_att, message, att)
        self._ensure_task_description_attachment_link(ir_att, target_model, target_id, att)
        self.get_mapping().set_mapping(
            compound_key, 'attachment', 'ir.attachment', ir_att.id,
        )
        try:
            os.remove(tmp_path)
        except OSError:
            self.log(f'WARN: could not remove tmp attachment file: {tmp_path}')
        return ir_att

    def run_resumable_batch(
        self, attachment_type='task', raw_attachments=None, start_index=0,
        deadline=None, tmp_dir=None, active_key=None, active_tmp_path=None,
        active_bytes=0, active_expected_size=0, max_items=None,
    ):
        """Process attachments with resumable chunked SFTP downloads."""
        if raw_attachments is None:
            self.log(f'Extracting Bitrix {attachment_type} attachments...')
            raw_attachments = self._get_raw_attachments(attachment_type)

        total = len(raw_attachments)
        index = int(start_index or 0)
        tmp_dir = tmp_dir or '/tmp/bitrix_attachment_tmp'
        processed = created = skipped = errors = 0
        files_downloaded = 0

        task_map, message_map, meeting_map = self._build_attachment_context(attachment_type)
        existing_att_mappings = self.get_mapping().get_all_mappings('attachment')
        self.log(
            f'Resumable batch start: type={attachment_type}, index={index}, '
            f'total={total}, existing={len(existing_att_mappings)}'
        )

        while index < total:
            if max_items is not None and processed >= max_items:
                break
            if self._deadline_reached(deadline):
                break

            att = self._row_to_attachment(attachment_type, raw_attachments[index])
            compound_key = self._make_compound_key(attachment_type, att)
            att_ref = self._format_attachment_ref(attachment_type, att, compound_key)
            self.log(f'Attachment resume start: index={index}, {att_ref}')

            existing_key = self._find_existing_attachment_key(
                compound_key, attachment_type, att, existing_att_mappings,
            )
            if existing_key:
                existing_att = self.env['ir.attachment'].sudo().browse(
                    existing_att_mappings.get(existing_key)
                ).exists()
                if existing_att and attachment_type == 'task':
                    res_model, res_id = self._resolve_parent(
                        attachment_type, att, task_map, message_map, meeting_map,
                    )
                    self._ensure_task_description_attachment_link(
                        existing_att, res_model, res_id, att,
                    )
                elif existing_att and attachment_type in ('comment', 'meeting_comment'):
                    res_model, res_id = self._resolve_parent(
                        attachment_type, att, task_map, message_map, meeting_map,
                    )
                    if res_model == 'mail.message' and res_id:
                        message = self.env['mail.message'].sudo().browse(res_id).exists()
                        if message:
                            message.write({'attachment_ids': [(4, existing_att.id)]})
                            self._ensure_message_body_attachment_link(
                                existing_att, message, att,
                            )
                skipped += 1
                processed += 1
                index += 1
                if active_key == compound_key and active_tmp_path:
                    try:
                        os.remove(active_tmp_path)
                    except OSError:
                        pass
                active_key = active_tmp_path = False
                active_bytes = active_expected_size = 0
                self.log(
                    f'Attachment skip existing mapping: matched_key={existing_key} '
                    f'processed={processed}'
                )
                continue

            res_model, res_id = self._resolve_parent(
                attachment_type, att, task_map, message_map, meeting_map,
            )
            if not res_id:
                errors += 1
                processed += 1
                index += 1
                self.log(
                    f'Attachment parent missing: {att_ref}, processed={processed}, '
                    f'errors={errors}'
                )
                continue

            tmp_path = active_tmp_path if active_key == compound_key and active_tmp_path else None
            if not tmp_path:
                tmp_path = self._default_tmp_path(tmp_dir, compound_key, att.file_name)
                active_bytes = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0

            status, bytes_written = self._download_file_chunked(
                att.file_path,
                tmp_path,
                offset=active_bytes or 0,
                expected_size=att.file_size,
                deadline=deadline,
                progress_callback=lambda current_bytes: self._report_resumable_progress({
                    'attachment_type': attachment_type,
                    'index': index,
                    'active_key': compound_key,
                    'active_tmp_path': tmp_path,
                    'active_bytes': current_bytes,
                    'active_expected_size': int(att.file_size or 0),
                }),
            )
            active_key = compound_key
            active_tmp_path = tmp_path
            active_bytes = bytes_written
            active_expected_size = int(att.file_size or 0)

            if status == 'partial':
                return {
                    'next_index': index,
                    'done': False,
                    'processed': processed,
                    'created': created,
                    'skipped': skipped,
                    'errors': errors,
                    'active_key': active_key,
                    'active_tmp_path': active_tmp_path,
                    'active_bytes': active_bytes,
                    'active_expected_size': active_expected_size,
                }

            if status != 'done':
                errors += 1
                processed += 1
                index += 1
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                active_key = active_tmp_path = False
                active_bytes = active_expected_size = 0
                continue

            ir_att = self._create_attachment_from_tmp(
                att, tmp_path, res_model, res_id, compound_key,
            )
            created += 1
            processed += 1
            files_downloaded += 1
            index += 1
            existing_att_mappings[compound_key] = ir_att.id
            active_key = active_tmp_path = False
            active_bytes = active_expected_size = 0
            self.log(
                f'Attachment created: ir_attachment_id={ir_att.id}, '
                f'created={created}, downloaded={files_downloaded}'
            )

        return {
            'next_index': index,
            'done': index >= total,
            'processed': processed,
            'created': created,
            'skipped': skipped,
            'errors': errors,
            'active_key': active_key or False,
            'active_tmp_path': active_tmp_path or False,
            'active_bytes': active_bytes or 0,
            'active_expected_size': active_expected_size or 0,
        }

    def run(self, attachment_type='task', raw_attachments=None):
        """Load attachments.

        Args:
            attachment_type: 'task', 'comment', 'meeting', or 'meeting_comment'
            raw_attachments: optional pre-fetched list
        """
        if raw_attachments is None:
            self.log(f'Extracting Bitrix {attachment_type} attachments...')
            raw_attachments = self._get_raw_attachments(attachment_type)
            if not raw_attachments and attachment_type not in (
                'task', 'comment', 'meeting', 'meeting_comment',
            ):
                return

        self.log(f'Found {len(raw_attachments)} {attachment_type} attachments')

        task_map, message_map, meeting_map = self._build_attachment_context(attachment_type)

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
                    att = self._row_to_attachment(attachment_type, row)

                    # Compound uniqueness key
                    compound_key = self._make_compound_key(attachment_type, att)
                    att_ref = self._format_attachment_ref(attachment_type, att, compound_key)
                    self.log(f'Attachment start: {att_ref}')
                    existing_key = self._find_existing_attachment_key(
                        compound_key,
                        attachment_type,
                        att,
                        existing_att_mappings,
                    )
                    if existing_key:
                        existing_att = self.env['ir.attachment'].sudo().browse(
                            existing_att_mappings.get(existing_key)
                        ).exists()
                        if existing_att and attachment_type == 'task':
                            res_model, res_id = self._resolve_parent(
                                attachment_type, att, task_map, message_map, meeting_map,
                            )
                            self._ensure_task_description_attachment_link(
                                existing_att, res_model, res_id, att,
                            )
                        elif existing_att and attachment_type in ('comment', 'meeting_comment'):
                            res_model, res_id = self._resolve_parent(
                                attachment_type, att, task_map, message_map, meeting_map,
                            )
                            if res_model == 'mail.message' and res_id:
                                message = self.env['mail.message'].sudo().browse(res_id).exists()
                                if message:
                                    message.write({'attachment_ids': [(4, existing_att.id)]})
                                    self._ensure_message_body_attachment_link(
                                        existing_att, message, att,
                                    )
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
                            message = self.env['mail.message'].sudo()
                            target_model = res_model
                            target_id = res_id
                            if res_model == 'mail.message':
                                message = message.browse(res_id).exists()
                                if message and message.model and message.res_id:
                                    target_model = message.model
                                    target_id = message.res_id

                            self.log(
                                f'Creating ir.attachment: {att_ref}, '
                                f'res_model={target_model}, res_id={target_id}'
                            )
                            ir_att = self.env['ir.attachment'].sudo().create({
                                'name': att.file_name,
                                'datas': file_data,
                                'res_model': target_model,
                                'res_id': target_id,
                                'mimetype': att.content_type,
                            })
                            if message:
                                message.write({'attachment_ids': [(4, ir_att.id)]})
                                self._ensure_message_body_attachment_link(ir_att, message, att)
                            self._ensure_task_description_attachment_link(
                                ir_att, target_model, target_id, att,
                            )
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
