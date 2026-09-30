"""Bitsy text checks that follow the editor's parser.

`parseWorld` in the creator throws when a room is missing a map row, a
drawing is missing a pixel row, a dialog quote never closes, or a room
places a sprite that was never defined. A document that survives
`parse_bitsy` is one the editor can load. A shorter "title and a room"
check accepts files that crash that parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAP_SIZE = 16
TILE_SIZE = 8

_MERGE_HEADER = {
    "room": "ROOM",
    "sprite": "SPR",
    "item": "ITM",
    "dialog": "DLG",
    "palette": "PAL",
}
_DRAWING = {"TIL", "SPR", "ITM"}


@dataclass
class BlockSpan:
    kind: str
    block_id: str
    start: int
    end: int


@dataclass
class BitsyDoc:
    lines: list[str]
    room_format: int
    spans: list[BlockSpan] = field(default_factory=list)
    rooms: int = 0


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_bitsy(text: str) -> BitsyDoc | None:
    """Return a document the editor can load, or None."""
    if not isinstance(text, str) or text == "":
        return None
    lines = normalize(text).split("\n")
    if not lines:
        return None
    doc = BitsyDoc(lines=lines, room_format=0)
    index = _read_title(lines)
    if index is None:
        return None
    sprite_ids: set[str] = set()
    placements: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line == "" or line.startswith("#"):
            index += 1
            continue
        kind = _arg(line, 0)
        if kind == "!":
            if _arg(line, 1) == "ROOM_FORMAT" and _arg(line, 2) == "1":
                doc.room_format = 1
            elif _arg(line, 1) == "ROOM_FORMAT":
                doc.room_format = 0
            index += 1
            continue
        if kind in {"ROOM", "SET"}:
            start = index
            parsed = _read_room(lines, index, doc.room_format)
            if parsed is None:
                return None
            index, placed = parsed
            placements.extend(placed)
            doc.rooms += 1
            doc.spans.append(BlockSpan("ROOM", _arg(line, 1) or "", start, index))
            continue
        if kind in _DRAWING:
            start = index
            block_id = _arg(line, 1) or ""
            index = _read_drawing_block(lines, index)
            if index is None:
                return None
            if kind == "SPR" and block_id:
                sprite_ids.add(block_id)
            if kind in {"SPR", "ITM"}:
                doc.spans.append(BlockSpan(kind, block_id, start, index))
            continue
        if kind == "PAL":
            start = index
            block_id = _arg(line, 1) or ""
            index = _read_until_blank(lines, index + 1)
            doc.spans.append(BlockSpan("PAL", block_id, start, index))
            continue
        if kind == "DLG":
            start = index
            block_id = _arg(line, 1) or ""
            index = _read_dialog(lines, index)
            if index is None:
                return None
            doc.spans.append(BlockSpan("DLG", block_id, start, index))
            continue
        if kind in {"TUNE", "BLIP", "FONT", "VAR", "END", "DEFAULT_FONT", "TEXT_DIRECTION"}:
            index = _read_until_blank(lines, index + 1)
            continue
        index += 1
    if doc.rooms < 1:
        return None
    if any(sprite_id not in sprite_ids for sprite_id in placements):
        return None
    return doc


def merge_change(source: str, kind: str, block_id: str, block: str) -> str | None:
    """Replace or append one piece. None means the editor must not apply it."""
    header = _MERGE_HEADER.get(kind)
    if header is None or not block_id or not isinstance(block, str):
        return None
    doc = parse_bitsy(source)
    if doc is None:
        return None
    piece = _normalize_block(block)
    if not _piece_ok(piece, header, block_id, doc.room_format):
        return None
    piece_lines = piece.split("\n")
    match = next(
        (span for span in doc.spans if span.kind == header and span.block_id == block_id),
        None,
    )
    lines = list(doc.lines)
    if match is not None:
        lines = lines[: match.start] + piece_lines + lines[match.end :]
    else:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(piece_lines)
    merged = "\n".join(lines)
    if parse_bitsy(merged) is None:
        return None
    return merged


def _piece_ok(piece: str, header: str, block_id: str, room_format: int) -> bool:
    lines = piece.split("\n")
    if not lines or lines[0].split(" ")[:2] != [header, block_id]:
        return False
    if header == "ROOM":
        end = _read_room(lines, 0, room_format)
        return end is not None and end[0] == len(lines)
    if header in {"SPR", "ITM"}:
        end = _read_drawing_block(lines, 0)
        return end is not None and end == len(lines)
    if header == "DLG":
        end = _read_dialog(lines, 0)
        return end is not None and end == len(lines)
    if header == "PAL":
        end = _read_until_blank(lines, 1)
        return end == len(lines) and _arg(lines[0], 1) == block_id
    return False


def _normalize_block(block: str) -> str:
    lines = normalize(block).split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _arg(line: str, index: int) -> str:
    parts = line.split(" ")
    if index >= len(parts):
        return ""
    return parts[index]


def _read_title(lines: list[str]) -> int | None:
    if not lines:
        return None
    index = _read_script(lines, 0)
    if index is None:
        return None
    return index + 1


def _read_script(lines: list[str], index: int) -> int | None:
    """Match scriptUtils.ReadDialogScript, and reject an unclosed quote."""
    if index >= len(lines):
        return None
    if lines[index] != '"""':
        return index + 1
    index += 1
    while index < len(lines) and lines[index] != '"""':
        index += 1
    if index >= len(lines):
        return None
    return index + 1


def _read_dialog(lines: list[str], index: int) -> int | None:
    if index >= len(lines) or not _arg(lines[index], 1):
        return None
    index = _read_script(lines, index + 1)
    if index is None:
        return None
    if index < len(lines) and lines[index] and _arg(lines[index], 0) == "NAME":
        index += 1
    return index


def _read_room(lines: list[str], index: int, room_format: int) -> tuple[int, list[str]] | None:
    if index >= len(lines) or _arg(lines[index], 0) not in {"ROOM", "SET"}:
        return None
    if not _arg(lines[index], 1):
        return None
    index += 1
    if index + MAP_SIZE > len(lines):
        return None
    for _ in range(MAP_SIZE):
        if room_format == 1 and lines[index] == "":
            return None
        index += 1
    placed: list[str] = []
    while index < len(lines) and lines[index] != "":
        if _arg(lines[index], 0) == "SPR":
            sprite_id = _arg(lines[index], 1)
            if sprite_id and "," not in sprite_id and len(lines[index].split(" ")) >= 3:
                placed.append(sprite_id)
        index += 1
    return index, placed


def _read_drawing_block(lines: list[str], index: int) -> int | None:
    if index >= len(lines) or _arg(lines[index], 0) not in _DRAWING:
        return None
    if not _arg(lines[index], 1):
        return None
    index = _read_drawing(lines, index + 1)
    if index is None:
        return None
    return _read_until_blank(lines, index)


def _read_drawing(lines: list[str], index: int) -> int | None:
    """Match parseDrawingCore: 8 pixel rows, then optional `>` frames."""
    y = 0
    while y < TILE_SIZE:
        if index + y >= len(lines):
            return None
        y += 1
        if y == TILE_SIZE:
            index = index + y
            if index < len(lines) and lines[index].startswith(">"):
                index += 1
                y = 0
    return index


def _read_until_blank(lines: list[str], index: int) -> int:
    while index < len(lines) and lines[index] != "":
        index += 1
    return index
