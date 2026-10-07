from __future__ import annotations

import hashlib
import io
import json
import re
from io import StringIO
import urllib.error
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.mail import SMTP_BACKEND
from app.hashers import SaltedSHA256PasswordHasher, WorkerPBKDF2PasswordHasher
from app.hashlib_compat import pbkdf2_hmac
from games.models import Game

User = get_user_model()

PASSWORD = "harbor-lantern-57"
LOC_MEM = "django.core.mail.backends.locmem.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"
SEND_URL = "https://api.cloudflare.com/client/v4/accounts/acct_123/email/sending/send"
CLOUDFLARE = {
    "CLOUDFLARE_ACCOUNT_ID": "acct_123",
    "CLOUDFLARE_EMAIL_API_TOKEN": "cf-test-token",
    "EMAIL_FROM": "noreply@chili.example",
    "FRONTEND_BASE_URL": "http://localhost:4200",
    "ON_WORKERS": True,
    "EMAIL_BACKEND": SMTP_BACKEND,
}
UNCONFIGURED = {
    "CLOUDFLARE_ACCOUNT_ID": "",
    "CLOUDFLARE_EMAIL_API_TOKEN": "",
    "EMAIL_FROM": "",
    "FRONTEND_BASE_URL": "http://localhost:4200",
    "ON_WORKERS": False,
    "EMAIL_BACKEND": LOC_MEM,
}


class _Body:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


SENT = {
    "success": True,
    "errors": [],
    "messages": [],
    "result": {"delivered": ["pepper@chili.example"], "permanent_bounces": [], "queued": []},
}


def _cloudflare_urlopen(calls, payload=SENT, status=200):
    def urlopen(request, timeout=30):
        calls.append(request)
        if request.full_url != SEND_URL:
            raise AssertionError(request.full_url)
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "error", {}, io.BytesIO(json.dumps(payload).encode())
            )
        return _Body(payload, status)

    return urlopen


def _message(request) -> dict:
    return json.loads(request.data.decode())


def _query(url: str) -> dict[str, str]:
    parsed = parse_qs(urlparse(url).query)
    return {key: values[0] for key, values in parsed.items()}


def _link_from_body(body: str) -> dict[str, str]:
    match = re.search(r"https?://\S+", body)
    if match is None:
        raise AssertionError(body)
    return _query(match.group(0))


@override_settings(**UNCONFIGURED)
class AccountApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_pbkdf2_compat_matches_openssl(self):
        password = b"harbor-lantern-57"
        salt = b"salt-value"
        self.assertEqual(
            pbkdf2_hmac("sha256", password, salt, 1000),
            hashlib.pbkdf2_hmac("sha256", password, salt, 1000),
        )

    def test_register_uses_password_validators_and_pbkdf2(self):
        weak = self.client.post(
            "/api/auth/register/",
            {
                "username": "pepper",
                "email": "pepper@chili.example",
                "password": "password",
                "accept_terms": True,
            },
            format="json",
        )
        self.assertEqual(weak.status_code, 400, weak.content)
        self.assertTrue(any("common" in message for message in weak.json()["password"]))

        numeric = self.client.post(
            "/api/auth/register/",
            {
                "username": "pepper",
                "email": "pepper@chili.example",
                "password": "12345678",
                "accept_terms": True,
            },
            format="json",
        )
        self.assertEqual(numeric.status_code, 400, numeric.content)
        self.assertTrue(any("numeric" in message for message in numeric.json()["password"]))

        short = self.client.post(
            "/api/auth/register/",
            {
                "username": "pepper",
                "email": "pepper@chili.example",
                "password": "short",
                "accept_terms": True,
            },
            format="json",
        )
        self.assertEqual(short.status_code, 400, short.content)

        similar = self.client.post(
            "/api/auth/register/",
            {
                "username": "pepper",
                "email": "pepper@chili.example",
                "password": "xxpepper",
                "accept_terms": True,
            },
            format="json",
        )
        self.assertEqual(similar.status_code, 400, similar.content)

        refused = self.client.post(
            "/api/auth/register/",
            {"username": "pepper", "email": "pepper@chili.example", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertIn("accept_terms", refused.json())
        declined = self.client.post(
            "/api/auth/register/",
            {
                "username": "pepper",
                "email": "pepper@chili.example",
                "password": PASSWORD,
                "accept_terms": False,
            },
            format="json",
        )
        self.assertEqual(declined.status_code, 400, declined.content)
        self.assertTrue(any("terms of service" in message for message in declined.json()["accept_terms"]))

        created = self._register()
        user = User.objects.get(username="pepper")
        self.assertFalse(user.email_verified)
        self.assertFalse(created.json()["user"]["email_verified"])
        self.assertIsNotNone(user.terms_accepted_at)
        self.assertEqual(user.privacy_accepted_at, user.terms_accepted_at)
        self.assertIsNone(user.seller_terms_accepted_at)
        self.assertIsNotNone(created.json()["user"]["terms_accepted_at"])
        self.assertIsNotNone(created.json()["user"]["privacy_accepted_at"])
        self.assertIsNone(created.json()["user"]["seller_terms_accepted_at"])
        self.assertTrue(user.password.startswith(f"pbkdf2_sha256${WorkerPBKDF2PasswordHasher.iterations}$"))
        self.assertIn("access", created.json())
        self.assertIn("refresh", created.json())

    def test_login_works_before_verification_and_writes_do_not(self):
        created = self._register()
        body = created.json()
        login = self.client.post(
            "/api/auth/token/",
            {"username": "pepper", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.content)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
        user = User.objects.get(username="pepper")
        game = Game.objects.create(owner=user, title="Draft", data="room 0")
        blocked = [
            self.client.post("/api/games/", {"title": "Moss", "data": "room"}, format="json"),
            self.client.put(
                f"/api/games/{game.id}/",
                {"title": "Draft", "data": "room 1"},
                format="json",
            ),
            self.client.post(f"/api/games/{game.id}/release/"),
            self.client.post("/api/forum/posts/", {"title": "Hi", "content": "There now"}, format="json"),
            self.client.post("/api/marketplace/listings/", {}, format="json"),
            self.client.post("/api/marketplace/listings/missing/checkout/", {}, format="json"),
            self.client.post("/api/marketplace/checkout/confirm/", {}, format="json"),
            self.client.post("/api/store/checkout/", {}, format="json"),
            self.client.post("/api/store/checkout/confirm/", {}, format="json"),
        ]
        for response in blocked:
            self.assertEqual(response.status_code, 403, response.content)
            self.assertEqual(response.json()["detail"], "Verify your email before doing that.")

        self.assertNotIn("verification_url", body)
        link = _link_from_body(mail.outbox[-1].body)
        self.assertIn("/verify-email?", mail.outbox[-1].body)
        verified = self.client.post("/api/auth/verify-email/", link, format="json")
        self.assertEqual(verified.status_code, 200, verified.content)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

        opened = self.client.post(
            "/api/games/",
            {"title": "Moss", "data": "room"},
            format="json",
        )
        self.assertEqual(opened.status_code, 201, opened.content)

    def test_resend_puts_the_link_in_the_mailbox(self):
        created = self._register()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {created.json()['access']}")
        before = len(mail.outbox)
        resent = self.client.post("/api/auth/verify-email/resend/")
        self.assertEqual(resent.status_code, 200, resent.content)
        self.assertEqual(resent.json(), {"detail": "Verification email sent."})
        self.assertNotIn("verification_url", resent.json())
        self.assertNotIn("token", resent.json())
        self.assertEqual(len(mail.outbox), before + 1)
        self.assertIn("/verify-email?", mail.outbox[-1].body)

    def test_password_reset_puts_the_link_in_the_mailbox(self):
        self._register()
        user = User.objects.get(username="pepper")
        self.assertFalse(user.email_verified)
        reset = self.client.post(
            "/api/auth/password/reset/",
            {"email": "pepper@chili.example"},
            format="json",
        )
        self.assertEqual(reset.status_code, 200, reset.content)
        self.assertNotIn("reset_url", reset.json())
        link = _link_from_body(mail.outbox[-1].body)
        self.assertIn("/reset-password?", mail.outbox[-1].body)
        weak = self.client.post(
            "/api/auth/password/reset/confirm/",
            {**link, "password": "12345678"},
            format="json",
        )
        self.assertEqual(weak.status_code, 400, weak.content)

        updated = self.client.post(
            "/api/auth/password/reset/confirm/",
            {**link, "password": "river-lantern-88"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertTrue(user.check_password("river-lantern-88"))
        denied = self.client.post(
            "/api/auth/token/",
            {"username": "pepper", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(denied.status_code, 401, denied.content)
        allowed = self.client.post(
            "/api/auth/token/",
            {"username": "pepper", "password": "river-lantern-88"},
            format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)

        missing = self.client.post(
            "/api/auth/password/reset/",
            {"email": "nobody@chili.example"},
            format="json",
        )
        self.assertEqual(missing.status_code, 200, missing.content)
        self.assertNotIn("reset_url", missing.json())
        self.assertNotIn("nobody@chili.example", mail.outbox[-1].body)

    def test_console_backend_prints_the_link(self):
        buffer = StringIO()
        with override_settings(EMAIL_BACKEND=CONSOLE):
            with patch("sys.stdout", buffer):
                response = self.client.post(
                    "/api/auth/register/",
                    {
                        "username": "pepper",
                        "email": "pepper@chili.example",
                        "password": PASSWORD,
                        "accept_terms": True,
                    },
                    format="json",
                )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("verification_url", response.json())
        printed = re.sub(r"\r?\n[ \t]", "", buffer.getvalue())
        self.assertIn("/verify-email?", printed)
        token = _link_from_body(printed)["token"]
        self.assertNotIn(token, response.content.decode())

    @override_settings(**{**CLOUDFLARE, "EMAIL_BACKEND": LOC_MEM})
    def test_worker_console_override_skips_cloudflare(self):
        with (
            patch("accounts.mail._workers_request", side_effect=AssertionError("cloudflare")),
            patch("accounts.mail.urllib.request.urlopen", side_effect=AssertionError("cloudflare")),
        ):
            response = self.client.post(
                "/api/auth/register/",
                {
                    "username": "pepper",
                    "email": "pepper@chili.example",
                    "password": PASSWORD,
                    "accept_terms": True,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("verification_url", response.json())
        self.assertIn("/verify-email?", mail.outbox[-1].body)
        self.assertIn("pepper@chili.example", mail.outbox[-1].to)

    @override_settings(ON_WORKERS=True, EMAIL_BACKEND=SMTP_BACKEND)
    def test_worker_without_mail_settings_returns_an_error_and_no_token(self):
        with patch("accounts.mail.urllib.request.urlopen", side_effect=AssertionError("network")):
            registered = self.client.post(
                "/api/auth/register/",
                {
                    "username": "pepper",
                    "email": "pepper@chili.example",
                    "password": PASSWORD,
                    "accept_terms": True,
                },
                format="json",
            )
            reset = self.client.post(
                "/api/auth/password/reset/",
                {"email": "pepper@chili.example"},
                format="json",
            )
        self.assertEqual(registered.status_code, 503, registered.content)
        self.assertEqual(registered.json(), {"detail": "Mail is not configured."})
        self.assertNotIn("verification_url", registered.json())
        self.assertNotIn("access", registered.json())
        self.assertFalse(User.objects.filter(username="pepper").exists())
        self.assertEqual(reset.status_code, 503, reset.content)
        self.assertEqual(reset.json(), {"detail": "Mail is not configured."})
        self.assertNotIn("reset_url", reset.json())

    def _post_register(self, username="pepper"):
        return self.client.post(
            "/api/auth/register/",
            {
                "username": username,
                "email": f"{username}@chili.example",
                "password": PASSWORD,
                "accept_terms": True,
            },
            format="json",
        )

    @override_settings(**{**CLOUDFLARE, "ON_WORKERS": False})
    def test_register_posts_to_cloudflare_and_hides_the_link(self):
        calls = []
        with patch("accounts.mail.urllib.request.urlopen", _cloudflare_urlopen(calls)):
            response = self._post_register()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("verification_url", response.json())
        self.assertEqual([request.full_url for request in calls], [SEND_URL])
        send = calls[0]
        self.assertEqual(send.get_method(), "POST")
        self.assertEqual(send.get_header("Authorization"), "Bearer cf-test-token")
        message = _message(send)
        self.assertEqual(message["to"], "pepper@chili.example")
        self.assertEqual(message["from"], "noreply@chili.example")
        self.assertIn("Verify", message["subject"])
        self.assertIn("/verify-email?", message["text"])
        link = re.search(r"http://localhost:4200/\S+", message["text"])
        self.assertIsNotNone(link)
        token = _query(link.group(0))["token"]
        self.assertNotIn(token, response.content.decode())

    @override_settings(**{**CLOUDFLARE, "ON_WORKERS": False})
    def test_password_reset_posts_to_cloudflare(self):
        User.objects.create_user(
            username="pepper", email="pepper@chili.example", password=PASSWORD, email_verified=True
        )
        calls = []
        with patch("accounts.mail.urllib.request.urlopen", _cloudflare_urlopen(calls)):
            response = self.client.post(
                "/api/auth/password/reset/", {"email": "pepper@chili.example"}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content)
        message = _message(calls[0])
        self.assertIn("/reset-password?", message["text"])
        self.assertEqual(message["to"], "pepper@chili.example")

    @override_settings(**{**CLOUDFLARE, "ON_WORKERS": True})
    def test_worker_cloudflare_uses_the_workers_http_client(self):
        calls = []

        def workers(method, url, headers, body):
            calls.append((method, url, headers.get("Authorization")))
            if url == SEND_URL:
                return 200, json.dumps(SENT)
            raise AssertionError(url)

        with patch("accounts.mail._workers_request", workers):
            response = self._post_register()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("verification_url", response.json())
        self.assertIn("access", response.json())
        self.assertEqual(calls, [("POST", SEND_URL, "Bearer cf-test-token")])

    @override_settings(**{**CLOUDFLARE, "ON_WORKERS": False})
    def test_cloudflare_rejection_returns_502_and_no_account(self):
        failure = {
            "success": False,
            "errors": [{"code": 10001, "message": "email.sending.error.invalid_request_schema"}],
            "messages": [],
            "result": None,
        }
        calls = []
        with patch("accounts.mail.urllib.request.urlopen", _cloudflare_urlopen(calls, failure, 400)):
            response = self._post_register()
        self.assertEqual(response.status_code, 502, response.content)
        self.assertEqual(response.json(), {"detail": "Could not send email."})
        self.assertFalse(User.objects.filter(username="pepper").exists())

    @override_settings(**{**CLOUDFLARE, "ON_WORKERS": False})
    def test_cloudflare_bounce_is_a_delivery_error(self):
        bounced = {
            "success": True,
            "errors": [],
            "messages": [],
            "result": {
                "delivered": [],
                "permanent_bounces": ["pepper@chili.example"],
                "queued": [],
            },
        }
        with patch("accounts.mail.urllib.request.urlopen", _cloudflare_urlopen([], bounced)):
            response = self._post_register()
        self.assertEqual(response.status_code, 502, response.content)

    @override_settings(**{**CLOUDFLARE, "ON_WORKERS": False})
    def test_cloudflare_unreachable_returns_502(self):
        with patch(
            "accounts.mail.urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            response = self._post_register()
        self.assertEqual(response.status_code, 502, response.content)

    @override_settings(**{**CLOUDFLARE, "CLOUDFLARE_EMAIL_API_TOKEN": "", "ON_WORKERS": True, "EMAIL_BACKEND": SMTP_BACKEND})
    def test_partial_cloudflare_settings_count_as_unconfigured(self):
        response = self._post_register()
        self.assertEqual(response.status_code, 503, response.content)

    def test_refresh_rotates_and_logout_revokes(self):
        user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password=PASSWORD,
            email_verified=True,
        )
        issued = self.client.post(
            "/api/auth/token/",
            {"username": user.username, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(issued.status_code, 200, issued.content)
        first = issued.json()["refresh"]
        rotated = self.client.post("/api/auth/token/refresh/", {"refresh": first}, format="json")
        self.assertEqual(rotated.status_code, 200, rotated.content)
        second = rotated.json()["refresh"]
        self.assertNotEqual(second, first)
        self.assertIn("access", rotated.json())
        reused = self.client.post("/api/auth/token/refresh/", {"refresh": first}, format="json")
        self.assertEqual(reused.status_code, 401, reused.content)

        logged_out = self.client.post("/api/auth/logout/", {"refresh": second}, format="json")
        self.assertEqual(logged_out.status_code, 200, logged_out.content)
        revoked = self.client.post("/api/auth/token/refresh/", {"refresh": second}, format="json")
        self.assertEqual(revoked.status_code, 401, revoked.content)

    def test_old_salted_sha256_hashes_still_verify_and_upgrade(self):
        user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password=PASSWORD,
            email_verified=True,
        )
        user.password = SaltedSHA256PasswordHasher().encode(PASSWORD, "oldsaltvalue1234")
        user.save(update_fields=["password"])
        self.assertTrue(user.check_password(PASSWORD))
        response = self.client.post(
            "/api/auth/token/",
            {"username": "pepper", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertTrue(
            user.password.startswith(f"pbkdf2_sha256${WorkerPBKDF2PasswordHasher.iterations}$")
        )

    def test_existing_user_is_not_backfilled_and_acceptance_sticks(self):
        user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password=PASSWORD,
            email_verified=True,
        )
        self.assertIsNone(user.terms_accepted_at)
        self.assertIsNone(user.privacy_accepted_at)
        self.assertIsNone(user.seller_terms_accepted_at)
        self.client.force_authenticate(user)

        public = self.client.get("/api/profiles/pepper/")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertNotIn("terms_accepted_at", public.json())
        self.assertNotIn("email", public.json())

        forged = self.client.put(
            "/api/profiles/me/",
            {
                "bio": "Still unsigned.",
                "avatar_url": "",
                "terms_accepted_at": "2020-01-01T00:00:00Z",
                "privacy_accepted_at": "2020-01-01T00:00:00Z",
                "seller_terms_accepted_at": "2020-01-01T00:00:00Z",
            },
            format="json",
        )
        self.assertEqual(forged.status_code, 200, forged.content)
        user.refresh_from_db()
        self.assertIsNone(user.terms_accepted_at)
        self.assertIsNone(user.seller_terms_accepted_at)
        self.assertIsNone(forged.json()["terms_accepted_at"])

        empty = self.client.post("/api/profiles/me/acceptance/", {}, format="json")
        self.assertEqual(empty.status_code, 400, empty.content)
        seller_first = self.client.post(
            "/api/profiles/me/acceptance/",
            {"seller_terms": True},
            format="json",
        )
        self.assertEqual(seller_first.status_code, 400, seller_first.content)
        self.assertIn("terms of service", seller_first.json()["detail"])

        accepted = self.client.post("/api/profiles/me/acceptance/", {"terms": True}, format="json")
        self.assertEqual(accepted.status_code, 200, accepted.content)
        user.refresh_from_db()
        first = user.terms_accepted_at
        self.assertIsNotNone(first)
        self.assertEqual(user.privacy_accepted_at, first)
        self.assertIsNone(user.seller_terms_accepted_at)

        repeat = self.client.post("/api/profiles/me/acceptance/", {"terms": True}, format="json")
        self.assertEqual(repeat.status_code, 200, repeat.content)
        user.refresh_from_db()
        self.assertEqual(user.terms_accepted_at, first)
        self.assertEqual(user.privacy_accepted_at, first)

    def _register(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "username": "pepper",
                "email": "pepper@chili.example",
                "password": PASSWORD,
                "accept_terms": True,
                "email_verified": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("verification_url", response.json())
        self.assertNotIn("token", response.json())
        self.assertFalse(response.json()["user"]["email_verified"])
        self.assertIn("/verify-email?", mail.outbox[-1].body)
        return response
