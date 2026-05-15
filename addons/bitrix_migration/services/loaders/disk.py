import base64
import logging
import os
from collections import defaultdict, deque

from ..normalizers.dto import BitrixDiskObject, BitrixDiskStorage
from .base import BaseLoader

_logger = logging.getLogger(__name__)


ROOT_FOLDER_NAME = 'Bitrix Import'

# Human-readable labels for b_disk_storage.ENTITY_TYPE (raw values are PHP class names).
ENTITY_TYPE_LABEL = {
    'Bitrix\\Disk\\ProxyType\\User': 'User',
    'Bitrix\\Disk\\ProxyType\\Common': 'Common',
    'Bitrix\\Disk\\ProxyType\\Group': 'Group',
    'Bitrix\\Im\\Disk\\ProxyType\\Im': 'IM',
}


class DiskLoader(BaseLoader):
    """Imports Bitrix Disk objects (b_disk_object + b_file) into
    document.folder / document.file of the paid odoo_document_management_cloud_sync
    module.

    Binaries are read from a locally-mounted copy of Bitrix's upload directory
    (provided by DevOps); MySQL is used only for the logical structure.
    """

    entity_type = 'disk'
    batch_size = 200

    def __init__(self, env, extractor, local_root, storage_filter=None,
                 include_trashed=False, log_callback=None):
        super().__init__(env, extractor, log_callback=log_callback)
        self.local_root = local_root
        self.storage_filter = storage_filter
        self.include_trashed = include_trashed
        self._folder_id_by_bid = {}
        self._admin_user_id = None

    def _admin_id(self):
        if self._admin_user_id is None:
            self._admin_user_id = self.env.ref('base.user_admin').id
        return self._admin_user_id

    def run(self):
        if not self.env['ir.model'].sudo().search([('model', '=', 'document.folder')], limit=1):
            raise RuntimeError(
                'document.folder model is not available — '
                'odoo_document_management_cloud_sync must be installed.'
            )
        root = self._ensure_root_folder()
        storages = self.extractor.get_disk_storages(self.storage_filter)
        self.log(f'Disk storages to import: {len(storages)}')
        for raw_storage in storages:
            try:
                storage = BitrixDiskStorage(**raw_storage)
            except Exception as e:
                self.log(f'ERR storage {raw_storage.get("external_id")}: {e}')
                self.error_count += 1
                continue
            storage_folder = self._ensure_storage_folder(root, storage)
            self._folder_id_by_bid = {}
            if storage.root_object_id:
                self._folder_id_by_bid[storage.root_object_id] = storage_folder.id
            self._import_storage(storage, storage_folder)

    def _ensure_root_folder(self):
        DocumentFolder = self.env['document.folder'].sudo()
        folder = DocumentFolder.with_context(active_test=False).search([
            ('name', '=', ROOT_FOLDER_NAME),
            ('parent_id', '=', False),
        ], limit=1)
        if not folder:
            folder = DocumentFolder.with_context(
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
                tracking_disable=True,
            ).create({
                'name': ROOT_FOLDER_NAME,
                'user_id': self._admin_id(),
            })
        return folder

    def _ensure_storage_folder(self, root, storage):
        suffix = storage.name or f'storage-{storage.external_id}'
        label = ENTITY_TYPE_LABEL.get(storage.entity_type, storage.entity_type or 'storage')
        name = f'Bitrix: {label} #{storage.external_id} — {suffix}'
        folder, _ = self.get_or_create(
            'document.folder',
            domain=[('parent_id', '=', root.id), ('name', '=', name)],
            vals={
                'name': name,
                'parent_id': root.id,
                'user_id': self._admin_id(),
            },
            bitrix_id=f'storage:{storage.external_id}',
            entity_type='disk_folder',
        )
        return folder

    def _import_storage(self, storage, storage_folder):
        rows = self.extractor.get_disk_objects(
            storage.external_id, include_trashed=self.include_trashed,
        )
        self.log(f'Storage #{storage.external_id} ({storage.name}): {len(rows)} objects')
        ordered = self._topo_sort(rows, storage)
        processed = 0
        last_bid = None
        for raw in ordered:
            try:
                obj = BitrixDiskObject(**raw)
            except Exception as e:
                self.error_count += 1
                self.errors.append((raw.get('external_id'), str(e)))
                self.log(f'ERR parse object {raw.get("external_id")}: {e}')
                continue
            try:
                if obj.type == 'folder':
                    self._upsert_folder(obj, storage_folder)
                else:
                    self._upsert_file(obj, storage_folder)
            except Exception as e:
                self.error_count += 1
                self.errors.append((obj.external_id, str(e)))
                self.log(f'ERR upsert {obj.type} {obj.external_id}: {e}')
            processed += 1
            last_bid = obj.external_id
            if processed % self.batch_size == 0:
                self.commit_checkpoint(processed, last_bid)
        self.commit_checkpoint(processed, last_bid)

    def _topo_sort(self, rows, storage):
        """Return rows ordered so that parents precede children.

        Roots are objects whose PARENT_ID is missing in the row set
        (typically the storage root itself, or already-processed parents).
        Cycles are detected and skipped with a log entry.
        """
        by_id = {}
        for r in rows:
            try:
                rid = int(r['external_id'])
            except (TypeError, KeyError, ValueError):
                continue
            by_id[rid] = r

        children = defaultdict(list)
        roots = []
        for rid, r in by_id.items():
            parent = r.get('parent_external_id')
            try:
                parent_int = int(parent) if parent else None
            except (TypeError, ValueError):
                parent_int = None
            if parent_int and parent_int in by_id:
                children[parent_int].append(rid)
            else:
                roots.append(rid)

        ordered = []
        visited = set()
        queue = deque(sorted(roots))
        while queue:
            rid = queue.popleft()
            if rid in visited:
                continue
            visited.add(rid)
            ordered.append(by_id[rid])
            for child in sorted(children.get(rid, [])):
                if child not in visited:
                    queue.append(child)

        missed = set(by_id) - visited
        if missed:
            self.log_once(
                f'topo_cycle:{storage.external_id}',
                f'topo: {len(missed)} object(s) skipped (cycle or unknown parent) '
                f'in storage {storage.external_id}',
            )
            for rid in sorted(missed):
                ordered.append(by_id[rid])
        return ordered

    def _resolve_parent(self, obj, storage_folder):
        if obj.parent_external_id and obj.parent_external_id in self._folder_id_by_bid:
            return self._folder_id_by_bid[obj.parent_external_id]
        if obj.parent_external_id:
            mapped = self.get_mapping().get_odoo_id(
                str(obj.parent_external_id), 'disk_folder',
            )
            if mapped:
                self._folder_id_by_bid[obj.parent_external_id] = mapped
                return mapped
        return storage_folder.id

    def _upsert_folder(self, obj, storage_folder):
        parent_odoo_id = self._resolve_parent(obj, storage_folder)
        folder, _ = self.get_or_create(
            'document.folder',
            domain=[('parent_id', '=', parent_odoo_id), ('name', '=', obj.name)],
            vals={
                'name': obj.name,
                'parent_id': parent_odoo_id,
                'user_id': self._admin_id(),
            },
            bitrix_id=str(obj.external_id),
            entity_type='disk_folder',
        )
        self._folder_id_by_bid[obj.external_id] = folder.id

    def _upsert_file(self, obj, storage_folder):
        if not obj.file_subdir or not obj.file_diskname:
            self.log_once(
                f'skip_no_bfile:{obj.external_id}',
                f'skip file id={obj.external_id} name={obj.name!r}: no b_file row',
            )
            self.skipped_count += 1
            return

        local_path = os.path.join(self.local_root, obj.file_subdir, obj.file_diskname)
        if not os.path.isfile(local_path):
            self.log_once(
                f'missing:{obj.file_subdir}/{obj.file_diskname}',
                f'missing on FS: {local_path}',
            )
            self.error_count += 1
            self.errors.append((obj.external_id, f'missing on FS: {local_path}'))
            return

        if self.dry_run:
            self.created_count += 1
            return

        with open(local_path, 'rb') as fh:
            data = base64.b64encode(fh.read())

        mapping_id = self.get_mapping().get_odoo_id(str(obj.external_id), 'disk_file')
        DocumentFile = self.env['document.file'].sudo()
        if mapping_id:
            existing = DocumentFile.with_context(active_test=False).browse(mapping_id).exists()
            if existing:
                existing.with_context(
                    mail_create_nolog=True,
                    mail_create_nosubscribe=True,
                    tracking_disable=True,
                ).write({
                    'file_data': data,
                    'name': obj.name,
                })
                self.updated_count += 1
                return

        parent_odoo_id = self._resolve_parent(obj, storage_folder)
        self.get_or_create(
            'document.file',
            domain=[('folder_id', '=', parent_odoo_id), ('name', '=', obj.name)],
            vals={
                'name': obj.name,
                'folder_id': parent_odoo_id,
                'file_data': data,
                'user_id': self._admin_id(),
            },
            bitrix_id=str(obj.external_id),
            entity_type='disk_file',
        )
