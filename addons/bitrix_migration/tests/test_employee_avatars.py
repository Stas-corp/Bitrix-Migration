import base64
import os
import shutil
import tempfile

from odoo.tests.common import TransactionCase

from ..services.loaders.employees import (
    EmployeeLoader,
    has_real_photo_image,
    is_svg_placeholder_image,
)


PNG_PIXEL = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2C4twAAAAASUVORK5CYII='
)
PNG_PIXEL_ALT = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII='
)


class _FakeExtractor:
    def __init__(self, avatar_rows):
        self.avatar_rows = avatar_rows

    def get_employee_avatars(self):
        return self.avatar_rows


class _FakeSFTPFile:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSFTP:
    def __init__(self, files):
        self.files = files

    def open(self, path, mode='rb'):
        if path not in self.files:
            raise FileNotFoundError(path)
        return _FakeSFTPFile(self.files[path])


class _UnexpectedSFTP:
    def open(self, path, mode='rb'):
        raise AssertionError(f'SFTP download must not happen for {path}')


class TestEmployeeAvatarSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _make_loader(self, employee, image_bytes, sftp=None):
        extractor = _FakeExtractor([{
            'user_id': employee.x_bitrix_id,
            'photo_path': '/upload/test/avatar.png',
            'content_type': 'image/png',
        }])
        loader = EmployeeLoader(
            self.env,
            extractor=extractor,
            sftp_host='bitrix-sftp',
            sftp_user='bitrix',
            sftp_base_path='/bitrix',
        )
        loader._get_sftp = lambda: sftp or _FakeSFTP({
            '/bitrix/upload/test/avatar.png': image_bytes,
        })
        loader._close_sftp = lambda: None
        return loader

    def test_sync_avatars_replaces_svg_placeholder_and_updates_partner(self):
        employee = self.env['hr.employee'].create({
            'name': 'Avatar Employee',
            'x_bitrix_id': 9001,
        })
        self.assertTrue(is_svg_placeholder_image(employee.image_1920))
        self.assertTrue(is_svg_placeholder_image(employee.work_contact_id.image_1920))

        user = self.env['res.users'].create({
            'name': 'Avatar User',
            'login': 'avatar.user@example.com',
            'email': 'avatar.user@example.com',
            'partner_id': employee.work_contact_id.id,
        })
        employee.write({'user_id': user.id})

        loader = self._make_loader(employee, PNG_PIXEL)
        loader.sync_avatars()

        employee.invalidate_recordset()
        user.invalidate_recordset()

        self.assertTrue(has_real_photo_image(employee.image_1920))
        self.assertFalse(is_svg_placeholder_image(employee.image_1920))
        self.assertEqual(employee.image_1920, employee.work_contact_id.image_1920)
        self.assertEqual(employee.image_1920, user.partner_id.image_1920)

    def test_sync_avatars_keeps_existing_real_photo(self):
        encoded_photo = base64.b64encode(PNG_PIXEL_ALT)
        employee = self.env['hr.employee'].create({
            'name': 'Existing Photo Employee',
            'x_bitrix_id': 9002,
            'image_1920': encoded_photo,
        })
        self.assertTrue(has_real_photo_image(employee.image_1920))
        self.assertEqual(employee.image_1920, employee.work_contact_id.image_1920)

        loader = self._make_loader(employee, PNG_PIXEL, sftp=_UnexpectedSFTP())
        loader.sync_avatars()

        employee.invalidate_recordset()
        self.assertEqual(employee.image_1920, encoded_photo)
        self.assertEqual(employee.work_contact_id.image_1920, encoded_photo)

    def test_sync_avatars_reads_local_upload_files(self):
        temp_root = tempfile.mkdtemp(prefix='bitrix-avatar-')
        try:
            local_dir = os.path.join(temp_root, 'test')
            os.makedirs(local_dir)
            avatar_path = os.path.join(local_dir, 'avatar.png')
            with open(avatar_path, 'wb') as avatar_file:
                avatar_file.write(PNG_PIXEL)

            employee = self.env['hr.employee'].create({
                'name': 'Local Avatar Employee',
                'x_bitrix_id': 9003,
            })

            extractor = _FakeExtractor([{
                'user_id': employee.x_bitrix_id,
                'photo_path': '/upload/test/avatar.png',
                'content_type': 'image/png',
            }])
            loader = EmployeeLoader(
                self.env,
                extractor=extractor,
                avatar_download_mode='local',
                avatar_local_root=temp_root,
            )

            loader.sync_avatars()

            employee.invalidate_recordset()
            self.assertTrue(has_real_photo_image(employee.image_1920))
            self.assertEqual(employee.image_1920, employee.work_contact_id.image_1920)
        finally:
            shutil.rmtree(temp_root)
