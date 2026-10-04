"""Per-route rate limits for public writes.

Authenticated reads are not throttled. Auth routes are keyed by client IP.
Signed-in writes are keyed by user. On a Worker the cache is per isolate, so
a Cloudflare rate-limiting rule is still required in front of these paths.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _configured_rate(scope: str) -> str:
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    try:
        return rates[scope]
    except KeyError as exc:
        raise ImproperlyConfigured(f"No throttle rate set for '{scope}' scope") from exc


def client_ip(request) -> str:
    """Prefer Cloudflare's client IP. Ignore a caller-supplied forwarding header."""
    cf_ip = (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip()
    if cf_ip:
        return cf_ip
    return (request.META.get("REMOTE_ADDR") or "unknown").strip() or "unknown"


class ClientIPThrottle(SimpleRateThrottle):
    def get_rate(self):
        return _configured_rate(self.scope)

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": client_ip(request)}


class AuthRegisterThrottle(ClientIPThrottle):
    scope = "auth_register"


class AuthTokenThrottle(ClientIPThrottle):
    scope = "auth_token"


class AuthRefreshThrottle(ClientIPThrottle):
    scope = "auth_refresh"


class UserWriteThrottle(UserRateThrottle):
    """Count POST, PUT, PATCH, and DELETE. Reads do not use up the budget."""

    def get_rate(self):
        return _configured_rate(self.scope)

    def allow_request(self, request, view):
        if request.method in _SAFE_METHODS:
            return True
        return super().allow_request(request, view)


class ForumWriteThrottle(UserWriteThrottle):
    scope = "forum_write"


class GameWriteThrottle(UserWriteThrottle):
    scope = "game_write"


class ListingWriteThrottle(UserWriteThrottle):
    scope = "listing_write"


class StoreCheckoutThrottle(UserWriteThrottle):
    scope = "store_checkout"


class MarketplaceCheckoutThrottle(UserWriteThrottle):
    scope = "marketplace_checkout"
