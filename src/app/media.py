from __future__ import annotations

import mimetypes

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404


def serve_media(request, name: str):
    cleaned = name.replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise Http404()
    if not default_storage.exists(cleaned):
        raise Http404()
    handle = default_storage.open(cleaned, "rb")
    content_type = mimetypes.guess_type(cleaned)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type)
    response["Cache-Control"] = "public, max-age=86400"
    return response
