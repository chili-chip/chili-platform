from __future__ import annotations

import mimetypes

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404

# Spreadsheets uploaded through the admin import wait here between preview and
# confirm (import_export.tmp_storages.MediaStorage). They are not public media.
PRIVATE_PREFIXES = ("django-import-export/",)


def serve_media(request, name: str):
    cleaned = name.replace("\\", "/").lstrip("/")
    if not cleaned or {".", ".."} & set(cleaned.split("/")):
        raise Http404()
    if cleaned.startswith(PRIVATE_PREFIXES):
        raise Http404()
    if not default_storage.exists(cleaned):
        raise Http404()
    handle = default_storage.open(cleaned, "rb")
    content_type = mimetypes.guess_type(cleaned)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type)
    response["Cache-Control"] = "public, max-age=86400"
    return response
