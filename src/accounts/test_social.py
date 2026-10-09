from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import SocialAccount
from accounts.social import STATE_SALT

User = get_user_model()

PROVIDERS = {
    "SOCIAL_AUTH_GITHUB_CLIENT_ID": "gh-id",
    "SOCIAL_AUTH_GITHUB_CLIENT_SECRET": "gh-secret",
    "SOCIAL_AUTH_GOOGLE_CLIENT_ID": "g-id",
    "SOCIAL_AUTH_GOOGLE_CLIENT_SECRET": "g-secret",
    "FRONTEND_BASE_URL": "https://chili.example",
}


def github(uid=42, login="pepper", emails=None, token_status=200, token=None):
    """Fake GitHub: token exchange, /user, /user/emails."""
    if emails is None:
        emails = [{"email": "pepper@chili.example", "primary": True, "verified": True}]
    calls = []

    def request(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url == "https://github.com/login/oauth/access_token":
            return token_status, token or {"access_token": "gho_test", "token_type": "bearer"}
        if url == "https://api.github.com/user":
            return 200, {"id": uid, "login": login, "name": "Pepper Chili"}
        if url == "https://api.github.com/user/emails":
            return 200, emails
        raise AssertionError(url)

    return request, calls


def google(sub="g-sub-1", email="pepper@chili.example", verified=True):
    def request(method, url, headers, body):
        if url == "https://oauth2.googleapis.com/token":
            return 200, {"access_token": "ya29.test", "id_token": "x"}
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return 200, {"sub": sub, "email": email, "email_verified": verified, "name": "Pep"}
        raise AssertionError(url)

    return request


@override_settings(**PROVIDERS)
class SocialLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def start(self, provider="github", **body):
        response = self.client.post(f"/api/auth/social/{provider}/start/", body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def finish(self, provider, state, fake, code="the-code"):
        with patch("accounts.social._request", side_effect=fake):
            return self.client.post(
                f"/api/auth/social/{provider}/callback/",
                {"code": code, "state": state},
                format="json",
            )

    def test_providers_lists_only_configured(self):
        self.assertEqual(
            [p["provider"] for p in self.client.get("/api/auth/social/providers/").data],
            ["github", "google"],
        )
        with override_settings(SOCIAL_AUTH_GOOGLE_CLIENT_SECRET=""):
            data = self.client.get("/api/auth/social/providers/").data
        self.assertEqual(data, [{"provider": "github", "label": "GitHub"}])

    def test_start_builds_authorize_url(self):
        data = self.start("github")
        url = urlparse(data["authorize_url"])
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.assertEqual(f"{url.netloc}{url.path}", "github.com/login/oauth/authorize")
        self.assertEqual(query["client_id"], "gh-id")
        self.assertEqual(query["redirect_uri"], "https://chili.example/auth/callback/github")
        self.assertEqual(query["state"], data["state"])
        self.assertNotIn("gh-secret", data["authorize_url"])

        google_url = self.start("google")["authorize_url"]
        self.assertTrue(google_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn("scope=openid+email+profile", google_url)

    def test_unknown_and_unconfigured_provider(self):
        self.assertEqual(self.client.post("/api/auth/social/myspace/start/").status_code, 404)
        with override_settings(SOCIAL_AUTH_GITHUB_CLIENT_ID=""):
            response = self.client.post("/api/auth/social/github/start/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "provider_not_configured")

    def test_github_sign_up_creates_verified_user(self):
        state = self.start("github", accept_terms=True)["state"]
        fake, calls = github()
        response = self.finish("github", state, fake)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["created"])
        self.assertIn("access", response.data)
        user = User.objects.get(email="pepper@chili.example")
        self.assertEqual(user.username, "pepper")
        self.assertEqual(user.display_name, "Pepper Chili")
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_usable_password())
        self.assertIsNotNone(user.terms_accepted_at)
        account = SocialAccount.objects.get(user=user)
        self.assertEqual((account.provider, account.uid), ("github", "42"))
        # Secret goes only to the token endpoint, with the same redirect_uri.
        token_body = parse_qs(calls[0][3])
        self.assertEqual(token_body["client_secret"], ["gh-secret"])
        self.assertEqual(token_body["redirect_uri"], ["https://chili.example/auth/callback/github"])

    def test_sign_up_without_terms_leaves_them_unaccepted(self):
        state = self.start("google")["state"]
        response = self.finish("google", state, google())
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data["user"]["terms_accepted_at"])

    def test_second_sign_in_returns_same_user(self):
        fake, _ = github()
        first = self.finish("github", self.start("github")["state"], fake)
        # The GitHub email changed; the account id still matches.
        fake, _ = github(emails=[{"email": "new@chili.example", "primary": True, "verified": True}])
        second = self.finish("github", self.start("github")["state"], fake)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["created"])
        self.assertEqual(first.data["user"]["id"], second.data["user"]["id"])
        self.assertEqual(User.objects.count(), 1)

    def test_username_collision_gets_suffix(self):
        User.objects.create_user("pepper", "other@chili.example", "harbor-lantern-57")
        fake, _ = github()
        response = self.finish("github", self.start("github")["state"], fake)
        self.assertEqual(response.status_code, 201)
        self.assertRegex(response.data["user"]["username"], r"^pepper\d{4}$")

    def test_links_to_existing_verified_email(self):
        user = User.objects.create_user("chef", "Pepper@chili.example", "harbor-lantern-57")
        user.email_verified = True
        user.save()
        response = self.finish("google", self.start("google")["state"], google())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["id"], user.pk)
        self.assertTrue(user.social_accounts.filter(provider="google").exists())
        self.assertTrue(User.objects.get(pk=user.pk).has_usable_password())

    def test_refuses_existing_unverified_email(self):
        User.objects.create_user("squatter", "pepper@chili.example", "harbor-lantern-57")
        response = self.finish("google", self.start("google")["state"], google())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "email_in_use")
        self.assertFalse(SocialAccount.objects.exists())

    def test_refuses_provider_without_verified_email(self):
        response = self.finish("google", self.start("google")["state"], google(verified=False))
        self.assertEqual(response.data["code"], "email_unverified")
        fake, _ = github(emails=[{"email": "x@chili.example", "primary": True, "verified": False}])
        response = self.finish("github", self.start("github")["state"], fake)
        self.assertEqual(response.data["code"], "email_unverified")
        self.assertFalse(User.objects.exists())

    def test_bad_state(self):
        fake, calls = github()
        response = self.finish("github", "forged", fake)
        self.assertEqual(response.data["code"], "state_invalid")
        # A Google state cannot finish a GitHub sign-in.
        response = self.finish("github", self.start("google")["state"], fake)
        self.assertEqual(response.data["code"], "state_invalid")
        old = signing.dumps({"p": "github", "n": "x"}, salt=STATE_SALT)
        with patch("django.core.signing.time.time", return_value=10**10):
            response = self.finish("github", old, fake)
        self.assertEqual(response.data["code"], "state_expired")
        self.assertEqual(calls, [])

    def test_rejected_code(self):
        fake, _ = github(token={"error": "bad_verification_code"})
        response = self.finish("github", self.start("github")["state"], fake)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "code_rejected")

    def test_inactive_user_cannot_sign_in(self):
        fake, _ = github()
        self.finish("github", self.start("github")["state"], fake)
        User.objects.update(is_active=False)
        response = self.finish("github", self.start("github")["state"], fake)
        self.assertEqual(response.status_code, 403)


@override_settings(**PROVIDERS)
class SocialConnectTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("chef", "chef@chili.example", "harbor-lantern-57")
        self.auth(self.user)

    def auth(self, user):
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def connect(self, provider, fake):
        state = self.client.post(
            f"/api/auth/social/{provider}/start/", {"link": True}, format="json"
        ).data["state"]
        with patch("accounts.social._request", side_effect=fake):
            return self.client.post(
                f"/api/auth/social/{provider}/callback/",
                {"code": "c", "state": state},
                format="json",
            )

    def test_link_requires_sign_in(self):
        response = APIClient().post("/api/auth/social/github/start/", {"link": True}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_connect_list_and_disconnect(self):
        fake, _ = github(emails=[])  # Connecting does not need a matching email.
        response = self.connect("github", fake)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["linked"])
        listed = self.client.get("/api/profiles/me/social/").data
        self.assertEqual([a["provider"] for a in listed], ["github"])
        self.assertEqual(listed[0]["login"], "pepper")
        self.assertNotIn("uid", listed[0])
        self.assertEqual(self.client.delete("/api/profiles/me/social/github/").status_code, 204)
        self.assertEqual(self.client.delete("/api/profiles/me/social/github/").status_code, 404)

    def test_link_state_needs_same_user(self):
        state = self.client.post(
            "/api/auth/social/github/start/", {"link": True}, format="json"
        ).data["state"]
        other = User.objects.create_user("other", "other@chili.example", "harbor-lantern-57")
        self.auth(other)
        fake, calls = github()
        with patch("accounts.social._request", side_effect=fake):
            response = self.client.post(
                "/api/auth/social/github/callback/", {"code": "c", "state": state}, format="json"
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(calls, [])
        self.assertFalse(SocialAccount.objects.exists())

    def test_cannot_take_account_linked_elsewhere(self):
        other = User.objects.create_user("other", "other@chili.example", "harbor-lantern-57")
        SocialAccount.objects.create(user=other, provider="github", uid="42")
        fake, _ = github()
        response = self.connect("github", fake)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "already_linked")

    def test_keeps_last_sign_in_method(self):
        self.user.set_unusable_password()
        self.user.save()
        SocialAccount.objects.create(user=self.user, provider="google", uid="g1")
        response = self.client.delete("/api/profiles/me/social/google/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "last_login_method")
        SocialAccount.objects.create(user=self.user, provider="github", uid="42")
        self.assertEqual(self.client.delete("/api/profiles/me/social/google/").status_code, 204)
