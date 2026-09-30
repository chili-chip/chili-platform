"""Owner assist for the Bitsy text on screen.

The browser sends the editor's current document. Workers AI returns one
piece (a room, sprite, item, dialog, or palette). This module merges that
piece and does not write the game. The editor applies a merged document
only when it still matches the text that was sent, then autosave stores it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from games.bitsy import merge_change, normalize, parse_bitsy
from games.covers import MAX_GAME_DATA_BYTES

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "@cf/qwen/qwen2.5-coder-32b-instruct"
MAX_ASSIST_PROJECT_CHARS = 48_000
MAX_PROMPT_CHARS = 64_000
CANNOT_MERGE = "The change could not be merged. The game was not changed."
UNAVAILABLE = "The assistant is unavailable."
NEEDS_LOGIN = (
    "The assistant is unavailable. Workers AI has no local simulator. "
    "Run `npx wrangler login`, then `npm run dev:ai`."
)
MODEL_FAILED = "The assistant could not answer. The game was not changed."
PROJECT_TOO_LARGE = "This project is too large for the assistant. The game was not changed."

SYSTEM_PROMPT = """You change one piece of a Bitsy game. Do not rewrite the whole game.

A piece is one of these blocks:
- room: ROOM, then 16 map rows, then optional NAME, PAL, SPR, or ITM lines.
- sprite: SPR, then 8 rows of 8 digits, then optional NAME, COL, POS, or DLG.
- item: ITM, then 8 rows of 8 digits, then optional NAME, COL, or DLG.
- dialog: DLG, then one line of text or a quoted block, then an optional NAME.
- palette: PAL, then color rows like 0,0,0, then an optional NAME.

Reply in this exact shape. Put the chat sentence first, on one line:

REPLY: one short sentence. Do not put Bitsy source in it.
KIND: room
ID: 0
BLOCK:
<the one replacement block, and nothing else>

KIND is room, sprite, item, dialog, or palette.
ID is that block's id, such as 0 or A.
When the game says `! ROOM_FORMAT 0`, each map row is 16 characters.
When it says `! ROOM_FORMAT 1`, each map row is 16 comma-separated ids.
"""

_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_REPLY_LINE = re.compile(r"^REPLY:\s*(.*)$", re.MULTILINE)
_KIND_LINE = re.compile(r"^KIND:\s*(\w+)\s*$", re.MULTILINE)
_ID_LINE = re.compile(r"^ID:\s*(\S+)\s*$", re.MULTILINE)
_BLOCK = re.compile(r"^BLOCK:\s*\n(.*)\Z", re.DOTALL | re.MULTILINE)


class ProjectTooLarge(Exception):
    """The on-screen project does not fit in the prompt. The game is unchanged."""


class AssistantUnavailable(Exception):
    """This process has no Workers AI binding."""


class AssistantError(Exception):
    """Workers AI failed. The game is unchanged."""


@dataclass
class ModelChange:
    reply: str = ""
    kind: str = ""
    block_id: str = ""
    block: str = ""


def assistant_unavailable_detail() -> str:
    """manage.py has no binding. A local Worker omits it until `wrangler login`."""
    from django.conf import settings

    if getattr(settings, "ON_WORKERS", False):
        return NEEDS_LOGIN
    return UNAVAILABLE


def model_name() -> str:
    from django.conf import settings

    configured = str(getattr(settings, "ASSISTANT_MODEL", "") or "").strip()
    return configured or DEFAULT_MODEL


def worker_env():
    """Return `workers.env`. Tests replace this; local manage.py has no AI binding."""
    from workers import env as env

    return env


def looks_like_bitsy(data: str) -> bool:
    """True when the editor's parser can load this document."""
    return parse_bitsy(data) is not None


def build_messages(
    game_data: str,
    message: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    document = game_data if game_data.strip() else "(This project has no Bitsy text yet.)"
    if re.search(r"^! ROOM_FORMAT 1\b", normalize(game_data), re.MULTILINE):
        room_rows = "Room map rows are 16 comma-separated ids."
    else:
        room_rows = "Room map rows are 16 characters with no commas."
    current = f"Current Bitsy game:\n{document}\n\n{room_rows}\n\nRequest:\n{message}"
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    messages.append({"role": "user", "content": current})
    return messages


def prepare_messages(
    game_data: str,
    *,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if len(game_data) > MAX_ASSIST_PROJECT_CHARS:
        raise ProjectTooLarge(PROJECT_TOO_LARGE)
    messages = build_messages(game_data, message, list(history or []))
    if sum(len(item["content"]) for item in messages) > MAX_PROMPT_CHARS:
        raise ProjectTooLarge(PROJECT_TOO_LARGE)
    return messages


def iter_assist_events(
    game_data: str,
    outcome: Any,
) -> Iterator[dict[str, str]]:
    """Yield reply text as it arrives, then the merged game or an error.

    `outcome` is whatever `AI.run` returned. A stream is read piece by piece.
    A finished string is split so the reply line is yielded before the merge.
    """
    splitter = _ReplySplitter()
    parts: list[str] = []
    try:
        for piece in iter_model_text(outcome):
            parts.append(piece)
            for reply in splitter.feed(piece):
                yield {"reply": reply}
    except AssistantError:
        yield {"reply": splitter.reply, "error": MODEL_FAILED}
        return

    change = parse_model_output("".join(parts))
    reply = change.reply or splitter.reply
    merged = ""
    if change.kind and change.block_id and change.block:
        merged = merge_change(game_data, change.kind, change.block_id, change.block) or ""
        if merged and len(merged.encode("utf-8")) > MAX_GAME_DATA_BYTES:
            merged = ""
    if merged:
        if not reply:
            reply = "Updated the Bitsy game."
        yield {"reply": reply, "data": merged}
        return
    if not reply:
        reply = "The assistant did not return a change."
    yield {"reply": reply, "error": CANNOT_MERGE}


def parse_model_output(text: str) -> ModelChange:
    stripped = text.strip()
    fenced = _FENCE.search(stripped)
    if stripped.startswith("{") or fenced:
        parsed = extract_json(stripped)
        if isinstance(parsed, dict) and ("block" in parsed or "kind" in parsed or "reply" in parsed):
            block = parsed.get("block")
            return ModelChange(
                reply=_clean_reply(parsed.get("reply")),
                kind=str(parsed.get("kind") or "").strip().lower(),
                block_id=str(parsed.get("id") or "").strip(),
                block=block if isinstance(block, str) else "",
            )
    reply_match = _REPLY_LINE.search(text)
    kind_match = _KIND_LINE.search(text)
    id_match = _ID_LINE.search(text)
    block_match = _BLOCK.search(text)
    block = block_match.group(1).strip("\n") if block_match else ""
    return ModelChange(
        reply=reply_match.group(1).strip() if reply_match else "",
        kind=kind_match.group(1).strip().lower() if kind_match else "",
        block_id=id_match.group(1).strip() if id_match else "",
        block=block,
    )


def run_workers_ai(messages: list[dict[str, str]]) -> Any:
    try:
        env = worker_env()
    except Exception as exc:
        raise AssistantUnavailable(UNAVAILABLE) from exc
    ai = getattr(env, "AI", None)
    try:
        run = getattr(ai, "run", None) if ai is not None else None
    except Exception as exc:
        raise AssistantUnavailable(UNAVAILABLE) from exc
    if not callable(run):
        raise AssistantUnavailable(UNAVAILABLE)

    # workers.env.AI is a binding wrapper. AI.run() starts a JS promise and
    # returns a coroutine. Create that promise inside the coroutine run_sync
    # is already driving, and call it as a method so `this` stays bound.
    # A promise started before that wait is an unhandled rejection, which the
    # Workers runtime turns into HTTP 502.
    options = {
        "messages": messages,
        "max_tokens": 1200,
        "temperature": 0.2,
        "stream": True,
    }

    async def call():
        outcome = ai.run(model_name(), options)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome

    try:
        return _pythonize(_drive(call()))
    except AssistantError:
        raise
    except Exception as exc:
        logger.exception("Workers AI assist call failed")
        raise AssistantError(MODEL_FAILED) from exc


def iter_model_text(outcome: Any) -> Iterator[str]:
    """Yield model text. Streams are deltas. One payload is split at the reply."""
    reader_factory = getattr(outcome, "getReader", None)
    if callable(reader_factory):
        yield from _iter_reader(reader_factory())
        return
    if isinstance(outcome, (list, tuple)):
        for part in outcome:
            yield str(part)
        return
    text = _as_text(outcome)
    if text:
        yield from _split_reply(text)


def extract_json(raw: Any) -> dict[str, Any] | None:
    value = _pythonize(raw)
    if isinstance(value, dict):
        if "reply" in value or "block" in value or "kind" in value:
            return value
        nested = value.get("response", value.get("result"))
        if isinstance(nested, dict):
            return nested
        if isinstance(nested, str):
            return _parse_json_text(nested)
        return None
    if isinstance(value, str):
        return _parse_json_text(value)
    return None


def encode_event(event: dict[str, str]) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


class _ReplySplitter:
    """Emit the REPLY line as soon as characters of it arrive."""

    def __init__(self) -> None:
        self.buffer = ""
        self.reply = ""
        self._done = False

    def feed(self, piece: str) -> list[str]:
        if self._done or not piece:
            self.buffer += piece
            return []
        self.buffer += piece
        match = re.search(r"REPLY:\s*", self.buffer)
        if match is None:
            return []
        rest = self.buffer[match.end() :]
        newline = rest.find("\n")
        visible = rest if newline < 0 else rest[:newline]
        updates: list[str] = []
        if visible != self.reply:
            self.reply = visible
            updates.append(visible)
        if newline >= 0:
            self._done = True
        return updates


def _split_reply(text: str) -> Iterator[str]:
    match = re.search(r"REPLY:\s*", text)
    if match is None:
        yield text
        return
    rest = text[match.end() :]
    newline = rest.find("\n")
    if newline < 0:
        yield text
        return
    reply = rest[:newline]
    if len(reply) <= 8:
        yield text
        return
    mid = max(1, len(reply) // 2)
    yield text[: match.end() + mid]
    yield text[match.end() + mid :]


def _iter_reader(reader: Any) -> Iterator[str]:
    pending = ""
    while True:
        raw = _drive(_read_stream_chunk(reader))
        if raw is None:
            break
        pending += raw
        delta, pending = _take_sse_deltas(pending)
        if delta:
            yield delta
    if pending.strip():
        delta, _rest = _take_sse_deltas(pending + "\n")
        if delta:
            yield delta


async def _read_stream_chunk(reader: Any) -> str | None:
    result = reader.read()
    if inspect.isawaitable(result):
        result = await result
    result = _pythonize(result)
    if isinstance(result, dict) and result.get("done"):
        return None
    value = result.get("value") if isinstance(result, dict) else result
    if value is None:
        return ""
    return _decode_chunk(value)


def _take_sse_deltas(buffer: str) -> tuple[str, str]:
    """Return complete SSE `response` deltas and the unparsed tail.

    A chunk that ends mid-line stays in the tail. Emitting it early would
    treat `data: {"response":` as reply text and drop the rest of the token.
    """
    if "\n" not in buffer:
        return "", buffer
    lines = buffer.split("\n")
    tail = lines.pop() if not buffer.endswith("\n") else ""
    parts: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            continue
        if stripped.startswith("data:"):
            payload = stripped[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                parts.append(payload)
                continue
            if isinstance(obj, dict) and isinstance(obj.get("response"), str):
                parts.append(obj["response"])
            continue
        parts.append(stripped)
    return "".join(parts), tail


def _as_text(outcome: Any) -> str:
    value = _pythonize(outcome)
    if isinstance(value, dict):
        nested = value.get("response", value.get("result"))
        if isinstance(nested, str):
            return nested
        if isinstance(nested, dict):
            return json.dumps(nested)
        if "reply" in value or "block" in value:
            return json.dumps(value)
        return ""
    if isinstance(value, str):
        return value
    return ""


def _decode_chunk(value: Any) -> str:
    value = _pythonize(value)
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, list) and value and isinstance(value[0], int):
        return bytes(int(item) & 0xFF for item in value).decode("utf-8", "replace")
    if isinstance(value, dict) and isinstance(value.get("response"), str):
        return value["response"]
    return ""


def _parse_json_text(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    fenced = _FENCE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _clean_reply(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:2000]


def _jspi_runner():
    """Return pyodide run_sync when this isolate can suspend, else None.

    The copy of run_sync outside the Worker raises NotImplementedError.
    Tests drive the same coroutine with asyncio instead of calling Workers AI.
    """
    try:
        from pyodide.ffi import can_run_sync, run_sync
    except Exception:
        return None
    try:
        if not can_run_sync():
            return None
    except Exception:
        return None
    return run_sync


def _drive(coro):
    runner = _jspi_runner()
    if runner is None:
        return asyncio.run(coro)
    return runner(coro)


def _pythonize(value: Any) -> Any:
    to_py = getattr(value, "to_py", None)
    if not callable(to_py):
        return value
    try:
        return to_py()
    except Exception:
        return value
