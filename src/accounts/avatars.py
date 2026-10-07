"""Avatar images stored in Django's default storage (R2 on the Worker).

The client sends a PNG or JPEG data URL, as the game creator does for covers.
The decoded file is checked against its magic bytes and a size cap. The user
row stores the absolute media URL.
"""

from __future__ import annotations

import base64
import binascii
import re
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from games.covers import absolute_media_url

MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_DIR = "avatars"

_DATA_URL = re.compile(
    r"^data:image/(?P<kind>png|jpeg);base64,(?P<payload>[A-Za-z0-9+/=\s]+)\Z",
    re.IGNORECASE,
)
_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff"}
_EXTENSION = {"png": "png", "jpeg": "jpg"}


def decode_avatar_data_url(value: str) -> tuple[bytes, str]:
    match = _DATA_URL.match(value.strip())
    if match is None:
        raise ValidationError("Avatar must be a PNG or JPEG data URL.")
    kind = match.group("kind").lower()
    payload = re.sub(r"\s+", "", match.group("payload"))
    if len(payload) > (MAX_AVATAR_BYTES * 4 // 3) + 4:
        raise ValidationError("Avatar must be 2 MB or smaller.")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("Avatar image is not valid base64.") from exc
    if not raw.startswith(_MAGIC[kind]):
        raise ValidationError("Avatar content does not match its image type.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise ValidationError("Avatar must be 2 MB or smaller.")
    return raw, _EXTENSION[kind]


def store_avatar(user, payload: bytes, extension: str, request=None) -> None:
    previous = _stored_name(user.avatar_url)
    name = f"{AVATAR_DIR}/{uuid4().hex}.{extension}"
    saved = default_storage.save(name, ContentFile(payload))
    user.avatar_url = absolute_media_url(default_storage.url(saved), request)
    user.save(update_fields=["avatar_url"])
    if previous and previous != saved:
        default_storage.delete(previous)


def clear_avatar(user) -> None:
    previous = _stored_name(user.avatar_url)
    user.avatar_url = ""
    user.save(update_fields=["avatar_url"])
    if previous:
        default_storage.delete(previous)


def _stored_name(url: str) -> str:
    """Storage name for an avatar we uploaded; empty for anything else."""
    marker = f"/{AVATAR_DIR}/"
    if marker not in url:
        return ""
    name = f"{AVATAR_DIR}/{url.rsplit(marker, 1)[1]}"
    return name if ".." not in name else ""
