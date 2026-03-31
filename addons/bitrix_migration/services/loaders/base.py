import logging
from itertools import islice

_logger = logging.getLogger(__name__)


class BaseLoader:
    """Base class for all migration loaders.

    Provides batch processing, idempotency (get_or_create),
    checkpoint/resume, and stats tracking.
    """

    entity_type = 'generic'
    batch_size = 500

    def __init__(self, env, extractor, batch_size=None, dry_run=False, log_callback=None):
        self.env = env
        self.extractor = extractor
        if batch_size:
            self.batch_size = batch_size
        self.dry_run = dry_run
        self.log_callback = log_callback

        self.created_count = 0
        self.updated_count = 0
        self.skipped_count = 0
        self.error_count = 0
        self.errors = []
        self._schema_cache = {}
        self._logged_once_messages = set()

    def log(self, message):
        _logger.info('[%s] %s', self.entity_type, message)
        if self.log_callback:
            self.log_callback(f'[{self.entity_type}] {message}')

    def log_once(self, key, message):
        """Log a message only once per loader instance."""
        if key in self._logged_once_messages:
            return
        self._logged_once_messages.add(key)
        self.log(message)

    def get_mapping(self):
        """Return the mapping model."""
        return self.env['bitrix.migration.mapping'].sudo()

    def db_table_exists(self, table_name):
        """Check whether a PostgreSQL table exists in the current schema."""
        cache_key = ('table', table_name)
        if cache_key not in self._schema_cache:
            self.env.cr.execute("SELECT to_regclass(%s)", (table_name,))
            self._schema_cache[cache_key] = bool(self.env.cr.fetchone()[0])
        return self._schema_cache[cache_key]

    def db_column_exists(self, table_name, column_name):
        """Check whether a PostgreSQL column exists."""
        cache_key = ('column', table_name, column_name)
        if cache_key not in self._schema_cache:
            self.env.cr.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s
                  AND column_name = %s
                LIMIT 1
                """,
                (table_name, column_name),
            )
            self._schema_cache[cache_key] = bool(self.env.cr.fetchone())
        return self._schema_cache[cache_key]

    def get_or_create(self, model_name, domain, vals, bitrix_id=None, entity_type=None):
        """Idempotent create: search first, create if not found.

        Returns (record, created: bool).
        """
        entity_type = entity_type or self.entity_type
        Model = self.env[model_name].sudo()

        existing = Model.with_context(active_test=False).search(domain, limit=1)
        if existing:
            self.skipped_count += 1
            return existing, False

        if self.dry_run:
            self.created_count += 1
            return Model, True

        try:
            record = Model.with_context(
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
                tracking_disable=True,
            ).create(vals)
        except Exception as e:
            self.error_count += 1
            self.errors.append((bitrix_id, str(e)))
            self.log(f'ERROR creating {model_name} bitrix_id={bitrix_id}: {e}')
            return Model, False

        if bitrix_id is not None:
            self.get_mapping().set_mapping(
                str(bitrix_id), entity_type, model_name, record.id,
            )

        self.created_count += 1
        return record, True

    def commit_checkpoint(self, processed_count, last_bitrix_id=None):
        """Commit current transaction and save checkpoint."""
        if self.dry_run:
            return
        self.env.cr.commit()
        if last_bitrix_id is not None:
            self.env['ir.config_parameter'].sudo().set_param(
                f'bitrix_migration.checkpoint.{self.entity_type}',
                str(last_bitrix_id),
            )
            self.env.cr.commit()
        self.log(f'Checkpoint: {processed_count} processed, '
                 f'{self.created_count} created, {self.skipped_count} skipped, '
                 f'{self.error_count} errors')

    def get_checkpoint(self):
        """Read last processed bitrix_id from checkpoint."""
        val = self.env['ir.config_parameter'].sudo().get_param(
            f'bitrix_migration.checkpoint.{self.entity_type}',
        )
        return val

    def clear_checkpoint(self):
        """Remove checkpoint for this entity type."""
        self.env['ir.config_parameter'].sudo().set_param(
            f'bitrix_migration.checkpoint.{self.entity_type}', '',
        )
        self.env.cr.commit()

    def log_stats(self):
        """Log final statistics."""
        self.log(
            f'DONE: created={self.created_count}, updated={self.updated_count}, '
            f'skipped={self.skipped_count}, errors={self.error_count}'
        )
        if self.errors:
            for bid, err in self.errors[:20]:
                self.log(f'  Error bitrix_id={bid}: {err}')
            if len(self.errors) > 20:
                self.log(f'  ... and {len(self.errors) - 20} more errors')

    def find_employee_by_bitrix_id(self, bitrix_user_id, employee_map=None):
        """Resolve hr.employee by Bitrix user id via mapping or x_bitrix_id."""
        if not bitrix_user_id:
            return self.env['hr.employee']

        Employee = self.env['hr.employee'].sudo().with_context(active_test=False)
        bid = str(bitrix_user_id)
        employee_map = employee_map or {}

        mapped_employee_id = employee_map.get(bid)
        if mapped_employee_id:
            employee = Employee.browse(mapped_employee_id).exists()
            if employee:
                return employee

        return Employee.search([('x_bitrix_id', '=', int(bitrix_user_id))], limit=1)

    def get_partner_from_employee(self, employee):
        """Return the best available partner linked to an employee."""
        if not employee:
            return self.env['res.partner']

        if 'work_contact_id' in employee._fields and employee.work_contact_id:
            return employee.work_contact_id
        if 'user_id' in employee._fields and employee.user_id and employee.user_id.partner_id:
            return employee.user_id.partner_id
        if 'address_home_id' in employee._fields and employee.address_home_id:
            return employee.address_home_id
        return self.env['res.partner']

    def get_user_from_employee(self, employee):
        """Return the linked res.users for an employee when available."""
        if not employee:
            return self.env['res.users']

        if 'user_id' in employee._fields and employee.user_id:
            return employee.user_id

        partner = self.get_partner_from_employee(employee)
        if partner:
            return self.env['res.users'].sudo().search(
                [('partner_id', '=', partner.id)], limit=1,
            )
        return self.env['res.users']

    def run(self):
        """Override in subclasses."""
        raise NotImplementedError

    @staticmethod
    def _batched(iterable, size):
        """Yield successive chunks of `size` from `iterable`."""
        it = iter(iterable)
        while True:
            batch = list(islice(it, size))
            if not batch:
                break
            yield batch
