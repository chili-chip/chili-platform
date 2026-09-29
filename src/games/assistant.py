"""Owner assist for a saved Bitsy project.

The Worker calls Workers AI with the game stored on the project. The browser
does not supply the document, and this module does not write the result.
The editor applies it and the existing autosave stores it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from games.covers import MAX_GAME_DATA_BYTES

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "@cf/qwen/qwen2.5-coder-32b-instruct"
MAX_ASSIST_PROJECT_CHARS = 48_000
MAX_PROMPT_CHARS = 64_000
INVALID_GAME = "The assistant did not return a Bitsy game."
UNAVAILABLE = "The assistant is unavailable."
# Workers AI cannot run inside local dev until the account is logged in.
NEEDS_LOGIN = (
    "The assistant is unavailable. Workers AI has no local simulator. "
    "Run `npx wrangler login`, then `npm run dev:ai`."
)
MODEL_FAILED = "The assistant could not answer. The game was not changed."
PROJECT_TOO_LARGE = "This project is too large for the assistant. The game was not changed."

SYSTEM_PROMPT = """You edit Bitsy games. Bitsy source is plain text:
- The first line is the title.
- Lines starting with # are comments. Lines starting with ! are flags.
- PAL blocks are palettes: three RGB colors and an optional NAME.
- ROOM blocks are rooms. A room has a map, then optional NAME, PAL, and SPR or ITM placements.
- TIL blocks are tiles, SPR blocks are sprites, and ITM blocks are items. Drawings are 8 by 8.
- DLG blocks are dialog, including the title dialog.

Reply with JSON only, no markdown. Use exactly two fields:
- reply: one short sentence for the chat. Do not put Bitsy source in reply.
- data: the full Bitsy document after the requested change.

Keep rooms, tiles, sprites, items, dialog, and palettes that the request does not change.
Stay inside what Bitsy text can express.
"""

_HEADER = re.compile(
    r"^(ROOM|PAL|TIL|SPR|ITM|DLG|WAL|END|DEFAULT_FONT|TEXT_DIRECTION)\b"
)
_ROOM = re.compile(r"^ROOM\s+\S+", re.MULTILINE)
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


class ProjectTooLarge(Exception):
    """The saved project does not fit in the prompt. The game is unchanged."""


class AssistantUnavailable(Exception):
    """This process has no Workers AI binding."""


class AssistantError(Exception):
    """Workers AI failed. The game is unchanged."""


@dataclass
class AssistResult:
    reply: str
    data: str = ""
    error: str = ""


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
    """A Bitsy game starts with a title and contains at least one room."""
    text = data.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return False
    first = text.split("\n", 1)[0].strip()
    if not first or first.startswith("#") or first.startswith("!"):
        return False
    if _HEADER.match(first):
        return False
    return _ROOM.search(text) is not None


def build_messages(
    game_data: str,
    message: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    document = game_data if game_data.strip() else "(This project has no Bitsy text yet.)"
    current = f"Current Bitsy game:\n{document}\n\nRequest:\n{message}"
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    messages.append({"role": "user", "content": current})
    return messages


def assist_project(
    game_data: str,
    *,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> AssistResult:
    if len(game_data) > MAX_ASSIST_PROJECT_CHARS:
        raise ProjectTooLarge(PROJECT_TOO_LARGE)
    messages = build_messages(game_data, message, list(history or []))
    prompt_size = sum(len(item["content"]) for item in messages)
    if prompt_size > MAX_PROMPT_CHARS:
        raise ProjectTooLarge(PROJECT_TOO_LARGE)

    parsed = extract_json(run_workers_ai(messages))
    reply = ""
    data = ""
    if isinstance(parsed, dict):
        reply = _clean_reply(parsed.get("reply"))
        candidate = parsed.get("data")
        if isinstance(candidate, str):
            data = candidate
    if not looks_like_bitsy(data) or len(data.encode("utf-8")) > MAX_GAME_DATA_BYTES:
        return AssistResult(reply=reply, error=INVALID_GAME)
    if not reply:
        reply = "Updated the Bitsy game."
    return AssistResult(reply=reply, data=data)


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
        "max_tokens": 8192,
        "temperature": 0.2,
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


def extract_json(raw: Any) -> dict[str, Any] | None:
    value = _pythonize(raw)
    if isinstance(value, dict):
        if "reply" in value or "data" in value:
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
