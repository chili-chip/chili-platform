"""Sign in with GitHub or Google (OAuth 2.0 authorization code flow).

The browser goes to the provider, which redirects back to the frontend at
``{FRONTEND_BASE_URL}/auth/callback/<provider>?code=...&state=...``. The
frontend posts ``code`` and ``state`` to the API, which swaps the code for a
provider token server side, reads the profile, and returns our own JWT pair.

``state`` is signed by Django, expires after ten minutes, and names the
provider and, when connecting from settings, the signed-in user. The frontend
also keeps the state it was given and checks the callback carries the same
one, which stops a stranger's code from signing this browser into their
account.

Every provider call goes through ``_request`` so it works on the Worker
(``workers.fetch``) and on host CPython (urllib), like the Stripe and mail
clients.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.legal import stamp_acceptance
from accounts.models import SocialAccount

logger = logging.getLogger(__name__)

User = get_user_model()

STATE_SALT = "accounts.social-state"
STATE_MAX_AGE = 60 * 10
USER_AGENT = "chili-platform"


class SocialAuthError(Exception):
    """Sign-in cannot finish. ``detail`` is safe to show; ``status`` is the HTTP code."""

    def __init__(self, detail: str, *, status: int = 400, code: str = "social_auth_failed"):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Profile:
    uid: str
    email: str
    email_verified: bool
    login: str
    name: str


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorize_url: str
    token_url: str
    scope: str

    @property
    def client_id(self) -> str:
        return str(getattr(settings, f"SOCIAL_AUTH_{self.name.upper()}_CLIENT_ID", "") or "")

    @property
    def client_secret(self) -> str:
        return str(getattr(settings, f"SOCIAL_AUTH_{self.name.upper()}_CLIENT_SECRET", "") or "")

    @property
    def configured(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())

    def redirect_uri(self) -> str:
        base = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:4200").rstrip("/")
        return f"{base}/auth/callback/{self.name}"

    def authorization_url(self, state: str) -> str:
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri(),
            "scope": self.scope,
            "state": state,
            "response_type": "code",
        }
        if self.name == SocialAccount.Provider.GOOGLE:
            query["prompt"] = "select_account"
        else:
            query["allow_signup"] = "true"
        return f"{self.authorize_url}?{urllib.parse.urlencode(query)}"


PROVIDERS: dict[str, Provider] = {
    SocialAccount.Provider.GITHUB: Provider(
        name=SocialAccount.Provider.GITHUB,
        label="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
    ),
    SocialAccount.Provider.GOOGLE: Provider(
        name=SocialAccount.Provider.GOOGLE,
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
    ),
}


def get_provider(name: str) -> Provider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise SocialAuthError("Unknown sign-in provider.", status=404, code="unknown_provider")
    if not provider.configured:
        raise SocialAuthError(
            f"{provider.label} sign-in is not available right now.",
            status=503,
            code="provider_not_configured",
        )
    return provider


# --- state ---------------------------------------------------------------


def make_state(provider: Provider, *, link_user=None, accept_terms: bool = False) -> str:
    payload: dict[str, Any] = {"p": provider.name, "n": secrets.token_urlsafe(16)}
    if link_user is not None:
        payload["link"] = link_user.pk
    if accept_terms:
        payload["terms"] = True
    return signing.dumps(payload, salt=STATE_SALT, compress=True)


def read_state(provider: Provider, state: str) -> dict[str, Any]:
    try:
        data = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise SocialAuthError(
            "This sign-in took too long. Please try again.", code="state_expired"
        ) from exc
    except signing.BadSignature as exc:
        raise SocialAuthError("This sign-in link is invalid.", code="state_invalid") from exc
    if not isinstance(data, dict) or data.get("p") != provider.name:
        raise SocialAuthError("This sign-in link is invalid.", code="state_invalid")
    return data


# --- provider calls --------------------------------------------------------


def fetch_profile(provider: Provider, code: str) -> Profile:
    token = _exchange_code(provider, code)
    if provider.name == SocialAccount.Provider.GITHUB:
        return _github_profile(token)
    return _google_profile(token)


def _exchange_code(provider: Provider, code: str) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "code": code,
            "redirect_uri": provider.redirect_uri(),
            "grant_type": "authorization_code",
        }
    )
    status, payload = _request(
        "POST",
        provider.token_url,
        {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        body,
    )
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if status >= 400 or not token:
        # GitHub answers 200 with {"error": "bad_verification_code"} for a used or old code.
        error = payload.get("error") if isinstance(payload, dict) else None
        logger.warning("%s token exchange failed: %s %s", provider.name, status, error)
        raise SocialAuthError(
            f"{provider.label} did not accept this sign-in. Please try again.",
            code="code_rejected",
        )
    return str(token)


def _github_profile(token: str) -> Profile:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    user = _get_json("https://api.github.com/user", headers, "GitHub")
    emails = _get_json("https://api.github.com/user/emails", headers, "GitHub")
    email = ""
    if isinstance(emails, list):
        verified = [e for e in emails if isinstance(e, dict) and e.get("verified")]
        primary = next((e for e in verified if e.get("primary")), None)
        chosen = primary or (verified[0] if verified else None)
        if chosen:
            email = str(chosen.get("email") or "")
    if not isinstance(user, dict) or user.get("id") is None:
        raise SocialAuthError("GitHub returned an unexpected profile.", status=502)
    return Profile(
        uid=str(user["id"]),
        email=email,
        email_verified=bool(email),
        login=str(user.get("login") or ""),
        name=str(user.get("name") or ""),
    )


def _google_profile(token: str) -> Profile:
    info = _get_json(
        "https://openidconnect.googleapis.com/v1/userinfo",
        {"Authorization": f"Bearer {token}", "Accept": "application/json"},
        "Google",
    )
    if not isinstance(info, dict) or not info.get("sub"):
        raise SocialAuthError("Google returned an unexpected profile.", status=502)
    email = str(info.get("email") or "")
    return Profile(
        uid=str(info["sub"]),
        email=email,
        email_verified=bool(email) and info.get("email_verified") is True,
        login=email.split("@")[0] if email else "",
        name=str(info.get("name") or ""),
    )


def _get_json(url: str, headers: dict[str, str], label: str) -> Any:
    status, payload = _request("GET", url, headers, None)
    if status >= 400:
        logger.warning("%s profile request %s returned %s", label, url, status)
        raise SocialAuthError(f"Could not read your {label} profile.", status=502)
    return payload


def _request(
    method: str, url: str, headers: dict[str, str], body: str | None
) -> tuple[int, Any]:
    if getattr(settings, "ON_WORKERS", False):
        status, text = _workers_request(method, url, headers, body)
    else:
        status, text = _urllib_request(method, url, headers, body)
    try:
        return status, json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise SocialAuthError("The sign-in provider sent an unreadable reply.", status=502) from exc


def _urllib_request(
    method: str, url: str, headers: dict[str, str], body: str | None
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
        raise SocialAuthError("Could not reach the sign-in provider.", status=502) from exc


def _workers_request(
    method: str, url: str, headers: dict[str, str], body: str | None
) -> tuple[int, str]:
    from pyodide.ffi import run_sync
    from workers import fetch

    kwargs: dict[str, Any] = {"method": method, "headers": headers}
    if body is not None:
        kwargs["body"] = body
    try:
        response = run_sync(fetch(url, **kwargs))
        text = run_sync(response.text())
    except Exception as exc:
        raise SocialAuthError("Could not reach the sign-in provider.", status=502) from exc
    return int(response.status), text


# --- accounts --------------------------------------------------------------


def link_account(user, provider: Provider, profile: Profile) -> SocialAccount:
    """Connect ``profile`` to a signed-in ``user`` from the settings page."""
    existing = SocialAccount.objects.filter(provider=provider.name, uid=profile.uid).first()
    if existing is not None:
        if existing.user_id != user.pk:
            raise SocialAuthError(
                f"This {provider.label} account is already connected to another user.",
                status=409,
                code="already_linked",
            )
        return existing
    if SocialAccount.objects.filter(user=user, provider=provider.name).exists():
        raise SocialAuthError(
            f"A different {provider.label} account is already connected. Disconnect it first.",
            status=409,
            code="provider_already_connected",
        )
    return SocialAccount.objects.create(
        user=user,
        provider=provider.name,
        uid=profile.uid,
        email=profile.email,
        login=profile.login,
        last_login_at=timezone.now(),
    )


def sign_in(provider: Provider, profile: Profile, *, accept_terms: bool) -> tuple[Any, bool]:
    """Return ``(user, created)`` for a provider sign-in.

    Order: a connected account; else an existing user with the same verified
    email; else a new user. Matching by email needs both sides verified, so a
    stranger who registered someone else's address and never confirmed it
    cannot have the real owner's sign-in land in their account.
    """
    account = (
        SocialAccount.objects.select_related("user")
        .filter(provider=provider.name, uid=profile.uid)
        .first()
    )
    if account is not None:
        account.last_login_at = timezone.now()
        account.email = profile.email or account.email
        account.login = profile.login or account.login
        account.save(update_fields=["last_login_at", "email", "login"])
        _check_active(account.user)
        return account.user, False

    if not profile.email or not profile.email_verified:
        raise SocialAuthError(
            f"Your {provider.label} account has no verified email address. "
            "Add one there, or sign up with email and password.",
            code="email_unverified",
        )

    existing = User.objects.filter(email__iexact=profile.email).first()
    if existing is not None:
        if not existing.email_verified:
            raise SocialAuthError(
                "An account with this email already exists but its email is not confirmed. "
                "Sign in with your password or reset it, then connect "
                f"{provider.label} from your settings.",
                status=409,
                code="email_in_use",
            )
        _check_active(existing)
        _create_account(existing, provider, profile)
        return existing, False

    with transaction.atomic():
        user = User(
            username=_unique_username(profile),
            email=profile.email,
            email_verified=True,
            display_name=" ".join(profile.name.split())[:50],
        )
        user.set_unusable_password()
        user.save()
        _create_account(user, provider, profile)
        if accept_terms:
            stamp_acceptance(user, terms=True)
    return user, True


def _create_account(user, provider: Provider, profile: Profile) -> SocialAccount:
    try:
        with transaction.atomic():
            return SocialAccount.objects.create(
                user=user,
                provider=provider.name,
                uid=profile.uid,
                email=profile.email,
                login=profile.login,
                last_login_at=timezone.now(),
            )
    except IntegrityError as exc:
        raise SocialAuthError(
            f"A different {provider.label} account is already connected to this user.",
            status=409,
            code="provider_already_connected",
        ) from exc


def _check_active(user) -> None:
    if not user.is_active:
        raise SocialAuthError("This account is disabled.", status=403, code="inactive")


_USERNAME_CHARS = re.compile(r"[^\w.@+-]")


def _unique_username(profile: Profile) -> str:
    base = _USERNAME_CHARS.sub("", profile.login or profile.email.split("@")[0])[:30]
    base = base or "player"
    candidate = base
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f"{base}{secrets.randbelow(9000) + 1000}"
    return candidate
