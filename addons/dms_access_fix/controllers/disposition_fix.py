# -*- coding: utf-8 -*-
"""Patch vendor DMS controllers so HTTP responses with non-ASCII filenames
don't crash werkzeug on Latin-1 encoding of Content-Disposition (RFC 7230).

HTTP headers must be Latin-1. The vendor module
(odoo_document_management_cloud_sync) builds ``Content-Disposition`` via
``f'attachment; filename="{name}"'`` where ``name`` is the raw
``document.file.name`` — frequently Cyrillic for Bitrix-imported files. The
encoder in werkzeug/http.server then raises UnicodeEncodeError when sending
the header.

We wrap the vendor route handlers and rewrite the header to RFC 5987 format
(``filename="ascii-fallback"; filename*=UTF-8''%xx%xx``) before the response
leaves Odoo. The check is a no-op if the header is already Latin-1 safe, so
the patch stays inert if the vendor fixes the bug upstream.
"""
from urllib.parse import quote

from odoo.addons.odoo_document_management_cloud_sync.controllers.document_api \
    import DocumentAPIController
from odoo.addons.odoo_document_management_cloud_sync.controllers.document_main \
    import DocumentManagementController


def _rfc5987(name, disposition='attachment'):
    safe = (name or 'download').replace('"', '').replace('\r', '').replace('\n', '')
    ascii_fallback = safe.encode('ascii', 'replace').decode('ascii').replace('?', '_')
    encoded = quote(safe, safe='')
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def _patch_disposition(response):
    if not hasattr(response, 'headers'):
        return response
    raw = response.headers.get('Content-Disposition')
    if not raw:
        return response
    try:
        raw.encode('latin-1')
        return response
    except UnicodeEncodeError:
        pass
    disposition = 'inline' if raw.lstrip().lower().startswith('inline') else 'attachment'
    name = 'download'
    marker = 'filename="'
    if marker in raw:
        start = raw.index(marker) + len(marker)
        end = raw.find('"', start)
        if end != -1:
            name = raw[start:end]
    response.headers['Content-Disposition'] = _rfc5987(name, disposition)
    return response


class DocumentAPIControllerPatched(DocumentAPIController):

    def download_file(self, file_id, **kwargs):
        return _patch_disposition(super().download_file(file_id, **kwargs))

    def view_file(self, file_id, **kwargs):
        return _patch_disposition(super().view_file(file_id, **kwargs))


class DocumentManagementControllerPatched(DocumentManagementController):

    def document_download(self, token, **kwargs):
        return _patch_disposition(super().document_download(token, **kwargs))
