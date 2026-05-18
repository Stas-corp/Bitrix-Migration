from odoo.tests.common import TransactionCase

from ..services.extractors.bitrix_mysql import BitrixMySQLExtractor


class TestDiskStorageFilters(TransactionCase):
    """Unit tests for BitrixMySQLExtractor._build_disk_storage_filters."""

    def _build(self, **kwargs):
        return BitrixMySQLExtractor._build_disk_storage_filters(**kwargs)

    def test_empty_no_filters(self):
        f = self._build()
        self.assertEqual(f['entity_clause'], '')
        self.assertEqual(f['user_join'], '')
        self.assertEqual(f['active_user_clause'], '')
        self.assertEqual(f['storage_clause'], '')
        self.assertEqual(f['params'], ())

    def test_single_entity_type(self):
        f = self._build(entity_types={'Common'})
        self.assertIn('s.ENTITY_TYPE LIKE %s', f['entity_clause'])
        self.assertEqual(f['entity_clause'].count('LIKE'), 1)
        self.assertEqual(f['params'], ('%ProxyType%Common',))

    def test_three_entity_types_sorted(self):
        f = self._build(entity_types={'User', 'Common', 'Group'})
        self.assertEqual(f['entity_clause'].count('LIKE'), 3)
        self.assertEqual(
            f['params'][:3],
            ('%ProxyType%Common', '%ProxyType%Group', '%ProxyType%User'),
        )

    def test_unknown_entity_type_raises(self):
        with self.assertRaises(ValueError) as cm:
            self._build(entity_types={'Bogus'})
        self.assertIn('Bogus', str(cm.exception))

    def test_active_users_only_with_user(self):
        f = self._build(entity_types={'User', 'Common'},
                        active_users_only=True)
        self.assertIn('LEFT JOIN b_user', f['user_join'])
        self.assertIn("u.ACTIVE = 'Y'", f['active_user_clause'])
        self.assertEqual(f['params'].count('%ProxyType%User'), 3)

    def test_active_users_only_without_user_is_noop(self):
        f = self._build(entity_types={'Common', 'Group'},
                        active_users_only=True)
        self.assertEqual(f['user_join'], '')
        self.assertEqual(f['active_user_clause'], '')

    def test_active_users_only_disabled(self):
        f = self._build(entity_types={'User'}, active_users_only=False)
        self.assertEqual(f['user_join'], '')
        self.assertEqual(f['active_user_clause'], '')
        self.assertEqual(f['params'], ('%ProxyType%User',))

    def test_storage_csv_filter(self):
        f = self._build(storage_filter=[174, 203])
        self.assertIn('s.ID IN (%s, %s)', f['storage_clause'])
        self.assertEqual(f['params'], (174, 203))

    def test_storage_csv_with_entity_and_active(self):
        f = self._build(
            storage_filter=[7],
            entity_types={'User'},
            active_users_only=True,
        )
        self.assertIn('LIKE', f['entity_clause'])
        self.assertIn('LEFT JOIN', f['user_join'])
        self.assertIn('s.ID IN', f['storage_clause'])
        self.assertEqual(f['params'][-1], 7)

    def test_sql_template_renders(self):
        f = self._build(entity_types={'Common', 'Group', 'User'},
                        active_users_only=True)
        params = f.pop('params')
        sql = BitrixMySQLExtractor.SQL_DISK_STORAGES_TEMPLATE.format(**f)
        self.assertIn('FROM b_disk_storage s', sql)
        self.assertIn('LEFT JOIN b_user u', sql)
        self.assertEqual(sql.count('%s'), len(params))
