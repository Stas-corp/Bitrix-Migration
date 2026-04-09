import base64
import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .base import BaseLoader

_logger = logging.getLogger(__name__)


def is_svg_placeholder_image(image_value):
    """Return True when the stored avatar is an SVG placeholder, not a real photo."""
    if not image_value:
        return False

    if isinstance(image_value, str):
        image_value = image_value.encode()

    try:
        raw = base64.b64decode(image_value, validate=False)
    except Exception:
        return False

    raw = raw.lstrip()
    return raw.startswith(b'<?xml') or raw.startswith(b'<svg')


def has_real_photo_image(image_value):
    """Return True when the stored image looks like an actual uploaded photo."""
    return bool(image_value) and not is_svg_placeholder_image(image_value)


class EmployeeLoader(BaseLoader):
    """Creates or updates hr.employee records for Bitrix employees."""

    entity_type = 'employee'
    batch_size = 200

    def __init__(self, env, extractor, user_map=None, dept_map=None,
                 sftp_host=None, sftp_port=22, sftp_user=None,
                 sftp_key_path=None, sftp_base_path='/home/bitrix/www',
                 avatar_download_mode='auto', avatar_local_root=None,
                 avatar_http_base_url=None, avatar_http_headers=None,
                 avatar_http_timeout=30,
                 **kwargs):
        super().__init__(env, extractor, **kwargs)
        # user_map: {str(bitrix_user_id): odoo_partner_id}
        self.user_map = user_map or {}
        # dept_map: {str(bitrix_dept_id): odoo_dept_id}
        self.dept_map = dept_map or {}
        # SFTP for avatar downloads
        self.sftp_host = sftp_host
        self.sftp_port = sftp_port
        self.sftp_user = sftp_user
        self.sftp_key_path = sftp_key_path
        self.sftp_base_path = (sftp_base_path or '/home/bitrix/www').rstrip('/')
        self._sftp = None
        self.avatar_download_mode = avatar_download_mode or 'auto'
        self.avatar_local_root = (avatar_local_root or '').rstrip('/')
        self.avatar_http_base_url = (avatar_http_base_url or '').rstrip('/')
        self.avatar_http_headers = self._parse_http_headers(avatar_http_headers)
        self.avatar_http_timeout = avatar_http_timeout or 30

    def run(self):
        self.log('Extracting Bitrix employees...')
        rows = self.extractor.get_employees()
        self.log(f'Found {len(rows)} active employees with department')

        if not rows:
            return

        # Telegram is loaded separately and is optional across Bitrix versions.
        self.log('Fetching Telegram accounts...')
        telegram_map = self.extractor.get_employee_telegrams()
        self.log(f'Found {len(telegram_map)} Telegram accounts')

        from ...services.normalizers.dto import BitrixEmployee

        employees = []
        for row in rows:
            try:
                employees.append(BitrixEmployee(**row))
            except Exception as e:
                self.log(f'ERROR parsing employee row {row}: {e}')

        mapping = self.get_mapping()
        existing = mapping.get_all_mappings('employee')
        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)

        processed = 0
        for batch in self._batched(employees, self.batch_size):
            for emp in batch:
                bid = str(emp.user_id)
                dept_id = self._resolve_dept(emp.dept_ids)
                odoo_user_id = self._resolve_user(emp.user_id)
                vals = self._build_employee_vals(
                    emp,
                    dept_id=dept_id,
                    odoo_user_id=odoo_user_id,
                    telegram=telegram_map.get(bid),
                )

                employee = None
                mapped_odoo_id = existing.get(bid)
                if mapped_odoo_id:
                    employee = Employee.browse(mapped_odoo_id).exists()

                if not employee:
                    employee = Employee.search([('x_bitrix_id', '=', emp.user_id)], limit=1)
                    if employee:
                        existing[bid] = employee.id
                        if not self.dry_run:
                            mapping.set_mapping(
                                bid, 'employee', 'hr.employee', employee.id,
                            )

                if employee:
                    self._update_employee(employee, emp.user_id, vals)
                else:
                    record, created = self.get_or_create(
                        'hr.employee',
                        [('x_bitrix_id', '=', emp.user_id)],
                        vals,
                        bitrix_id=emp.user_id,
                        entity_type='employee',
                    )
                    if record:
                        employee = record
                    if created and record:
                        existing[bid] = record.id

                if employee and not self.dry_run:
                    self._sync_related_records(employee)

                processed += 1

            self.commit_checkpoint(processed)

        self.log_stats()

    def _build_employee_vals(self, emp, dept_id=None, odoo_user_id=None, telegram=None):
        """Map Bitrix employee contacts to the closest Odoo employee fields."""
        vals = {
            'name': emp.full_name,
            'work_email': emp.email or '',
            'work_phone': emp.work_phone or '',
            'mobile_phone': emp.mobile_phone or emp.personal_phone or '',
            'x_bitrix_id': emp.user_id,
        }

        telegram = (telegram or '').strip()
        if telegram:
            vals['x_bitrix_telegram'] = telegram
        if dept_id:
            vals['department_id'] = dept_id
        if odoo_user_id:
            vals['user_id'] = odoo_user_id

        return vals

    def _prepare_update_vals(self, employee, vals):
        """Keep reruns safe: fill missing data and sync changed source values."""
        update_vals = {}

        for field_name, value in vals.items():
            if value in (None, False, ''):
                continue

            if field_name in ('department_id', 'user_id'):
                current_value = employee[field_name].id if employee[field_name] else False
            else:
                current_value = employee[field_name] or False

            if current_value != value:
                update_vals[field_name] = value

        return update_vals

    def _update_employee(self, employee, bitrix_id, vals):
        update_vals = self._prepare_update_vals(employee, vals)
        if not update_vals:
            self.skipped_count += 1
            return

        if self.dry_run:
            self.updated_count += 1
            return

        try:
            employee.write(update_vals)
            self.updated_count += 1
        except Exception as e:
            self.error_count += 1
            self.errors.append((bitrix_id, str(e)))
            self.log(f'ERROR updating hr.employee bitrix_id={bitrix_id}: {e}')

    def _sync_related_records(self, employee):
        """Relink migrated history and task assignees once employee links exist."""
        partner = self.get_partner_from_employee(employee)
        if partner:
            self._sync_comment_authors(employee, partner.id)

        user = self.get_user_from_employee(employee)
        if user:
            self._sync_task_assignees(employee, user.id)

    def _sync_comment_authors(self, employee, partner_id):
        if not self.db_column_exists('mail_message', 'x_bitrix_author_employee_id'):
            return

        Message = self.env['mail.message'].sudo().with_context(active_test=False)
        messages = Message.search([
            ('x_bitrix_author_employee_id', '=', employee.id),
            ('x_bitrix_author_id', '!=', False),
        ])
        for message in messages:
            message.write({
                'author_id': partner_id,
                'x_bitrix_author_id': False,
            })

    def _sync_task_assignees(self, employee, user_id):
        """Refresh task assignees/access for tasks affected by this employee's user mapping.

        Responsible + accomplice contribute to assignee user_ids.
        Auditors contribute to access users.
        """
        Task = self.env['project.task'].sudo().with_context(active_test=False)

        has_responsible_field = 'x_bitrix_responsible_employee_id' in Task._fields
        has_accomplice_field = 'x_bitrix_accomplice_employee_ids' in Task._fields
        has_auditor_field = 'x_bitrix_auditor_employee_ids' in Task._fields

        if not has_responsible_field and not has_accomplice_field and not has_auditor_field:
            return

        # Find tasks where this employee is canonical responsible or accomplice
        domain_parts = []
        if has_responsible_field:
            domain_parts.append(('x_bitrix_responsible_employee_id', '=', employee.id))
        if has_accomplice_field:
            domain_parts.append(('x_bitrix_accomplice_employee_ids', 'in', employee.id))
        if has_auditor_field:
            domain_parts.append(('x_bitrix_auditor_employee_ids', 'in', employee.id))

        if len(domain_parts) > 1:
            domain = ['|'] * (len(domain_parts) - 1) + domain_parts
        else:
            domain = domain_parts

        tasks = Task.search(domain)
        for task in tasks:
            self.recompute_task_user_ids(task)
            if hasattr(task, '_sync_bitrix_user_access'):
                task._sync_bitrix_user_access(mirror_assignee_users=False)

    def _resolve_dept(self, dept_ids):
        """Return Odoo hr.department id for first known dept_id."""
        Department = self.env['hr.department'].sudo().with_context(active_test=False)
        for did in dept_ids:
            odoo_id = self.dept_map.get(str(did))
            if odoo_id and Department.browse(odoo_id).exists():
                return odoo_id
        return None

    def _resolve_user(self, bitrix_user_id):
        """Return res.users.id for a Bitrix user_id via partner mapping."""
        partner_id = self.user_map.get(str(bitrix_user_id))
        if not partner_id:
            return None
        user = self.env['res.users'].sudo().search(
            [('partner_id', '=', partner_id)], limit=1
        )
        return user.id if user else None

    # ── Avatar support ───────────────────────────────────────────────

    def _get_sftp(self):
        if self._sftp is not None:
            return self._sftp
        if not self.sftp_host:
            return None
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
            return self._sftp
        except Exception as e:
            self.log(f'SFTP connection failed for avatars: {e}')
            return None

    def _close_sftp(self):
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

    def sync_avatars(self):
        """Download and set employee avatars from Bitrix.

        Policy:
        - keep existing real photos;
        - replace Odoo-generated SVG placeholders with the Bitrix photo;
        - keep employee/contact/user partner avatars in sync.
        """
        sources = self._get_avatar_sources()
        if not sources:
            self.log('Skipping avatars: no avatar source configured')
            return

        avatar_rows = self.extractor.get_employee_avatars()
        if not avatar_rows:
            self.log('No employee avatars found in Bitrix')
            return

        self.log(f'Found {len(avatar_rows)} employee avatars in Bitrix')
        self.log(f'Avatar sources: {", ".join(sources)}')
        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)

        imported = 0
        propagated = 0
        skipped = 0
        errors = 0

        try:
            for row in avatar_rows:
                user_id = str(row['user_id'])
                photo_path = row.get('photo_path', '')
                if not photo_path:
                    continue

                employee = Employee.search([('x_bitrix_id', '=', int(user_id))], limit=1)
                if not employee:
                    skipped += 1
                    continue

                partner_targets = self._get_avatar_partner_targets(employee)
                existing_real_image = self._get_existing_real_avatar(
                    employee, partner_targets,
                )
                if existing_real_image:
                    if self._write_avatar_targets(
                        employee, existing_real_image, partner_targets,
                    ):
                        propagated += 1
                    else:
                        skipped += 1
                    continue

                try:
                    data = self._download_avatar(photo_path)
                    encoded = base64.b64encode(data)
                    if self._write_avatar_targets(
                        employee, encoded, partner_targets,
                    ):
                        imported += 1
                    else:
                        skipped += 1
                except FileNotFoundError:
                    errors += 1
                    _logger.warning('Avatar file not found for user %s: %s', user_id, photo_path)
                except Exception as e:
                    errors += 1
                    _logger.warning('Avatar download error for user %s: %s', user_id, e)

                if (imported + propagated) % 50 == 0 and (imported + propagated) > 0:
                    self.env.cr.commit()

        finally:
            self._close_sftp()

        self.env.cr.commit()
        self.log(
            f'Avatars: imported={imported}, propagated={propagated}, '
            f'skipped={skipped}, errors={errors}'
        )

    def _get_avatar_partner_targets(self, employee):
        partners = self.env['res.partner']
        partner = self.get_partner_from_employee(employee)
        if partner:
            partners |= partner

        user = self.get_user_from_employee(employee)
        if user and user.partner_id:
            partners |= user.partner_id

        return partners

    def _get_existing_real_avatar(self, employee, partner_targets):
        if has_real_photo_image(employee.image_1920):
            return employee.image_1920

        for partner in partner_targets:
            if has_real_photo_image(partner.image_1920):
                return partner.image_1920

        return False

    def _write_avatar_targets(self, employee, image_value, partner_targets):
        updated = False

        if employee.image_1920 != image_value:
            employee.write({'image_1920': image_value})
            updated = True

        for partner in partner_targets:
            if partner.image_1920 != image_value:
                partner.write({'image_1920': image_value})
                updated = True

        return updated

    def _parse_http_headers(self, raw_headers):
        if not raw_headers:
            return {}

        raw_headers = raw_headers.strip()
        if not raw_headers:
            return {}

        try:
            parsed = json.loads(raw_headers)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            return {
                str(key).strip(): str(value).strip()
                for key, value in parsed.items()
                if str(key).strip() and str(value).strip()
            }

        headers = {}
        for line in raw_headers.splitlines():
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                headers[key] = value

        return headers

    def _get_avatar_sources(self):
        mode = self.avatar_download_mode or 'auto'
        if mode == 'sftp':
            return ['sftp'] if self.sftp_host else []
        if mode == 'local':
            return ['local'] if self._has_local_avatar_root() else []
        if mode == 'http':
            return ['http'] if self.avatar_http_base_url else []

        sources = []
        if self._has_local_avatar_root():
            sources.append('local')
        if self.sftp_host:
            sources.append('sftp')
        if self.avatar_http_base_url:
            sources.append('http')
        return sources

    def _has_local_avatar_root(self):
        for root in self._get_local_avatar_roots():
            if os.path.isdir(root):
                return True
        return False

    def _get_local_avatar_roots(self):
        roots = []
        if self.avatar_local_root:
            roots.append(self.avatar_local_root)
        if self.sftp_base_path:
            roots.append(self.sftp_base_path)
        return roots

    def _download_avatar(self, photo_path):
        last_error = None

        for source in self._get_avatar_sources():
            try:
                if source == 'local':
                    return self._download_avatar_from_local(photo_path)
                if source == 'sftp':
                    return self._download_avatar_from_sftp(photo_path)
                if source == 'http':
                    return self._download_avatar_from_http(photo_path)
            except Exception as e:
                last_error = e

        if last_error:
            raise last_error
        raise FileNotFoundError(photo_path)

    def _download_avatar_from_sftp(self, photo_path):
        sftp = self._get_sftp()
        if not sftp:
            raise FileNotFoundError(photo_path)

        full_path = self.sftp_base_path + photo_path
        with sftp.open(full_path, 'rb') as sftp_file:
            return sftp_file.read()

    def _download_avatar_from_local(self, photo_path):
        for candidate_path in self._get_local_avatar_paths(photo_path):
            if not os.path.isfile(candidate_path):
                continue
            with open(candidate_path, 'rb') as local_file:
                return local_file.read()

        raise FileNotFoundError(photo_path)

    def _get_local_avatar_paths(self, photo_path):
        candidates = []
        normalized_path = (photo_path or '').strip()
        if not normalized_path:
            return candidates

        relative_upload_path = normalized_path
        if normalized_path.startswith('/upload/'):
            relative_upload_path = normalized_path[len('/upload/'):]

        if self.avatar_local_root:
            candidates.append(
                os.path.join(self.avatar_local_root, relative_upload_path.lstrip('/'))
            )
            candidates.append(
                os.path.join(self.avatar_local_root, normalized_path.lstrip('/'))
            )

        if self.sftp_base_path:
            candidates.append(
                os.path.join(self.sftp_base_path, normalized_path.lstrip('/'))
            )

        unique_candidates = []
        seen = set()
        for candidate in candidates:
            normalized_candidate = os.path.normpath(candidate)
            if normalized_candidate in seen:
                continue
            seen.add(normalized_candidate)
            unique_candidates.append(normalized_candidate)
        return unique_candidates

    def _download_avatar_from_http(self, photo_path):
        target_url = self._get_avatar_http_url(photo_path)
        if not target_url:
            raise FileNotFoundError(photo_path)

        headers = {'User-Agent': 'Mozilla/5.0'}
        headers.update(self.avatar_http_headers)
        request = Request(target_url, headers=headers)

        try:
            with urlopen(request, timeout=self.avatar_http_timeout) as response:
                return response.read()
        except HTTPError:
            raise
        except URLError:
            raise

    def _get_avatar_http_url(self, photo_path):
        normalized_path = (photo_path or '').strip()
        if not normalized_path:
            return ''
        if normalized_path.startswith(('http://', 'https://')):
            return normalized_path
        if not self.avatar_http_base_url:
            return ''
        return urljoin(f'{self.avatar_http_base_url}/', normalized_path.lstrip('/'))
