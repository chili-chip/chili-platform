"""PNG covers for saved games.

The creator sends a PNG data URL. The decoded file goes through Django's
default storage — R2 on the Worker, the local media directory in development —
and the model stores only that path. Responses return a media URL.
"""

from __future__ import annotations

import base64
import binascii
import re
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_GAME_DATA_BYTES = 1_500_000

_PNG_DATA_URL = re.compile(
    r"^data:image/png;base64,(?P<payload>[A-Za-z0-9+/=\s]+)\Z",
    re.IGNORECASE,
)
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def absolute_media_url(url: str, request=None) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if request is not None:
        return request.build_absolute_uri(url)
    base = getattr(settings, "PUBLIC_BASE_URL", "http://localhost:8787").rstrip("/")
    path = url if url.startswith("/") else f"/{url}"
    return f"{base}{path}"


def decode_png_data_url(value: str) -> bytes:
    match = _PNG_DATA_URL.match(value.strip())
    if match is None:
        raise ValidationError("Cover must be a PNG data URL.")
    payload = re.sub(r"\s+", "", match.group("payload"))
    if len(payload) > (MAX_COVER_BYTES * 4 // 3) + 4:
        raise ValidationError("Cover must be 8 MB or smaller.")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("Cover image is not valid base64.") from exc
    if not raw.startswith(_PNG_MAGIC):
        raise ValidationError("Cover must be a PNG image.")
    if len(raw) > MAX_COVER_BYTES:
        raise ValidationError("Cover must be 8 MB or smaller.")
    return raw


def assign_cover(game, payload: bytes) -> None:
    previous = game.cover.name if game.cover else ""
    content = ContentFile(payload, name=f"{uuid4().hex}.png")
    content.content_type = "image/png"
    game.cover.save(content.name, content, save=False)
    if previous and previous != game.cover.name:
        game.cover.storage.delete(previous)


def clear_cover(game) -> None:
    if game.cover:
        game.cover.delete(save=False)
