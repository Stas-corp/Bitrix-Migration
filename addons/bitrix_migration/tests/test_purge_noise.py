"""Tests for EmployeeLoader.purge_noise_accounts()."""
from odoo.tests.common import TransactionCase

from ..services.loaders.employees import EmployeeLoader


class _NoiseExtractor:
    """Extractor stub: returns a fixed list of noise bitrix user_ids."""

    def __init__(self, noise_ids):
        self.noise_ids = noise_ids

    def get_noise_user_ids(self):
        return [{'user_id': i} for i in self.noise_ids]


class _NoCommitLoader(EmployeeLoader):
    def commit_checkpoint(self, count, last_bitrix_id=None):  # noqa: D401
        return None


class TestPurgeNoise(TransactionCase):
    """Verify that purge_noise_accounts removes orphan hr.employee/partner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context={
            'mail_create_nolog': True,
            'mail_create_nosubscribe': True,
            'tracking_disable': True,
        })

    def _make_loader(self, noise_ids, dry_run=False):
        return _NoCommitLoader(
            self.env, _NoiseExtractor(noise_ids), dry_run=dry_run,
        )

    def test_noise_employee_purged_with_partner(self):
        """Noise employee + its orphan work_contact_id are deleted."""
        noise = self.env['hr.employee'].create({
            'name': 'imconnector_xxx',
            'x_bitrix_id': 991001,
        })
        real = self.env['hr.employee'].create({
            'name': 'Real Employee',
            'x_bitrix_id': 991002,
        })
        noise_partner = noise.work_contact_id
        real_partner = real.work_contact_id
        self.assertTrue(noise_partner)
        self.assertTrue(real_partner)

        self._make_loader([991001]).purge_noise_accounts()

        self.assertFalse(
            self.env['hr.employee'].with_context(active_test=False).search(
                [('x_bitrix_id', '=', 991001)],
            )
        )
        self.assertFalse(noise_partner.exists())
        # Real employee untouched.
        self.assertTrue(real.exists())
        self.assertTrue(real_partner.exists())

    def test_dry_run_does_not_delete(self):
        noise = self.env['hr.employee'].create({
            'name': 'imopenlines_yyy',
            'x_bitrix_id': 991003,
        })
        partner = noise.work_contact_id

        self._make_loader([991003], dry_run=True).purge_noise_accounts()

        self.assertTrue(noise.exists())
        self.assertTrue(partner.exists())

    def test_partner_shared_with_other_employee_kept(self):
        """If work_contact_id is shared, partner is NOT deleted."""
        shared_partner = self.env['res.partner'].create({'name': 'Shared P'})
        noise = self.env['hr.employee'].create({
            'name': 'noise',
            'x_bitrix_id': 991004,
            'work_contact_id': shared_partner.id,
        })
        other = self.env['hr.employee'].create({
            'name': 'other',
            'x_bitrix_id': 991005,
            'work_contact_id': shared_partner.id,
        })

        self._make_loader([991004]).purge_noise_accounts()

        self.assertFalse(noise.exists())
        self.assertTrue(other.exists())
        self.assertTrue(shared_partner.exists(),
                        'shared partner must NOT be deleted')

    def test_mail_message_author_reassigned_to_system_partner(self):
        """mail.message with x_bitrix_author_employee_id=noise gets author switched."""
        noise = self.env['hr.employee'].create({
            'name': 'noise with messages',
            'x_bitrix_id': 991006,
        })
        partner = noise.work_contact_id
        odoobot = self.env.ref('base.partner_root')

        if 'x_bitrix_author_employee_id' not in self.env['mail.message']._fields:
            self.skipTest('x_bitrix_author_employee_id not present')

        task = self.env['project.task'].create({'name': 'host task'})
        message = self.env['mail.message'].create({
            'model': 'project.task',
            'res_id': task.id,
            'body': '<p>noise comment</p>',
            'author_id': partner.id,
            'x_bitrix_author_employee_id': noise.id,
        })

        self._make_loader([991006]).purge_noise_accounts()

        message.invalidate_recordset()
        self.assertEqual(message.author_id, odoobot)
        self.assertFalse(message.x_bitrix_author_employee_id)
        self.assertFalse(noise.exists())

    def test_creator_unset_on_tasks(self):
        noise = self.env['hr.employee'].create({
            'name': 'creator-noise',
            'x_bitrix_id': 991007,
        })
        task = self.env['project.task'].create({
            'name': 'task by noise',
            'x_bitrix_creator_employee_id': noise.id,
        })

        self._make_loader([991007]).purge_noise_accounts()

        task.invalidate_recordset()
        self.assertFalse(task.x_bitrix_creator_employee_id)
        self.assertTrue(task.exists())  # task itself survives
