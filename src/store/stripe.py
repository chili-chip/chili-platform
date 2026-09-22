"""Minimal Stripe REST client that works on CPython and Cloudflare Workers.

The official Stripe SDK needs sockets. Workers use `workers.fetch` plus
`pyodide.ffi.run_sync` from a Django WSGI view. Local tests use urllib.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

STRIPE_API_VERSION = "2026-07-29.dahlia"


class StripeError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


def flatten_params(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Encode nested dicts/lists the way Stripe's form API expects."""
    items: list[tuple[str, str]] = []
    if value is None:
        return items
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}[{key}]" if prefix else str(key)
            items.extend(flatten_params(nested, path))
        return items
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            items.extend(flatten_params(nested, f"{prefix}[{index}]"))
        return items
    if isinstance(value, bool):
        items.append((prefix, "true" if value else "false"))
        return items
    items.append((prefix, str(value)))
    return items


def _encode_params(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    return urllib.parse.urlencode(flatten_params(data))


def _secret_key() -> str:
    key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""
    if not key:
        raise StripeError("Stripe is not configured.", status_code=503)
    return key


def _on_workers() -> bool:
    return bool(getattr(settings, "ON_WORKERS", False))


def _request(method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    encoded = _encode_params(data)
    url = f"https://api.stripe.com{path}"
    if method == "GET" and encoded:
        url = f"{url}?{encoded}"
        body: str | None = None
    else:
        body = encoded if method != "GET" else None

    headers = {
        "Authorization": f"Bearer {_secret_key()}",
        "Stripe-Version": STRIPE_API_VERSION,
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    if _on_workers():
        status, text = _workers_request(method, url, headers, body)
    else:
        status, text = _urllib_request(method, url, headers, body)

    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise StripeError(
            "Stripe returned a non-JSON response.",
            status_code=status,
        ) from exc

    if status >= 400:
        error = payload.get("error") or {}
        message = error.get("message") or text or "Stripe request failed."
        raise StripeError(message, status_code=status, payload=payload)
    if not isinstance(payload, dict):
        raise StripeError("Unexpected Stripe response.", status_code=status)
    return payload


def _urllib_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: str | None,
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8") if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise StripeError(f"Could not reach Stripe: {exc.reason}") from exc


def _workers_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: str | None,
) -> tuple[int, str]:
    from pyodide.ffi import run_sync
    from workers import fetch

    kwargs: dict[str, Any] = {"method": method, "headers": headers}
    if body is not None:
        kwargs["body"] = body
    response = run_sync(fetch(url, **kwargs))
    text = run_sync(response.text())
    return int(response.status), text


def create_checkout_session(params: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "/v1/checkout/sessions", params)


def retrieve_checkout_session(session_id: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(session_id, safe="")
    return _request("GET", f"/v1/checkout/sessions/{quoted}")


def verify_webhook_payload(payload: bytes, signature_header: str, secret: str) -> dict[str, Any]:
    if not secret:
        raise StripeError("Stripe webhook secret is not configured.", status_code=503)
    if not signature_header:
        raise StripeError("Missing Stripe-Signature header.", status_code=400)

    parsed: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key and value:
            parsed.setdefault(key, []).append(value)

    try:
        timestamp = int((parsed.get("t") or [""])[0])
    except (TypeError, ValueError) as exc:
        raise StripeError("Invalid Stripe-Signature timestamp.", status_code=400) from exc

    signatures = parsed.get("v1") or []
    if not signatures:
        raise StripeError("Invalid Stripe-Signature header.", status_code=400)

    tolerance = getattr(settings, "STRIPE_WEBHOOK_TOLERANCE", 300)
    if abs(int(time.time()) - timestamp) > tolerance:
        raise StripeError("Webhook timestamp is outside the allowed tolerance.", status_code=400)

    signed = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise StripeError("Invalid Stripe webhook signature.", status_code=400)

    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StripeError("Webhook body is not valid JSON.", status_code=400) from exc
    if not isinstance(event, dict):
        raise StripeError("Webhook body is not a JSON object.", status_code=400)
    return event
