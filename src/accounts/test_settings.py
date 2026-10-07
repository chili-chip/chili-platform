from __future__ import annotations

import base64
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserSettings
from games.models import Game

User = get_user_model()

PASSWORD = "harbor-lantern-57"
NEW_PASSWORD = "copper-meadow-82"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _data_url(raw: bytes, kind: str = "png") -> str:
    return f"data:image/{kind};base64,{base64.b64encode(raw).decode()}"


class ProfileSettingsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password=PASSWORD,
            email_verified=True,
            bio="Hot sauce maker.",
        )

    def test_settings_default_and_update(self):
        self.client.force_authenticate(self.user)
        got = self.client.get("/api/profiles/me/settings/")
        self.assertEqual(got.status_code, 200, got.content)
        self.assertEqual(got.json()["theme"], "system")
        self.assertFalse(got.json()["newsletter_opt_in"])
        self.assertTrue(got.json()["show_bio"])

        patched = self.client.patch(
            "/api/profiles/me/settings/",
            {"newsletter_opt_in": True, "theme": "dark", "locale": "pl"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        prefs = UserSettings.objects.get(user=self.user)
        self.assertTrue(prefs.newsletter_opt_in)
        self.assertIsNotNone(prefs.newsletter_updated_at)
        self.assertEqual(prefs.theme, "dark")

    def test_settings_reject_bad_values_and_anonymous(self):
        self.assertEqual(self.client.get("/api/profiles/me/settings/").status_code, 401)
        self.client.force_authenticate(self.user)
        for body in ({"theme": "neon"}, {"locale": "xx"}):
            res = self.client.patch("/api/profiles/me/settings/", body, format="json")
            self.assertEqual(res.status_code, 400, res.content)

    def test_display_name_is_trimmed_and_shown_publicly(self):
        self.client.force_authenticate(self.user)
        res = self.client.patch(
            "/api/profiles/me/", {"display_name": "  Pepper   Pot "}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["display_name"], "Pepper Pot")
        public = APIClient().get("/api/profiles/pepper/")
        self.assertEqual(public.json()["display_name"], "Pepper Pot")
        too_long = self.client.patch("/api/profiles/me/", {"display_name": "x" * 51}, format="json")
        self.assertEqual(too_long.status_code, 400)

    def test_privacy_switches_hide_fields_from_public_profile(self):
        UserSettings.objects.create(
            user=self.user, show_bio=False, show_joined=False, show_games=False
        )
        public = APIClient().get("/api/profiles/pepper/").json()
        self.assertEqual(public["bio"], "")
        self.assertIsNone(public["created_at"])
        self.assertFalse(public["show_games"])
        self.client.force_authenticate(self.user)
        own = self.client.get("/api/profiles/me/").json()
        self.assertEqual(own["bio"], "Hot sauce maker.")

    def test_hidden_games_are_not_listed_for_others(self):
        Game.objects.create(owner=self.user, title="Hot Level", released=True)
        listed = APIClient().get("/api/games/?username=pepper")
        self.assertEqual(len(listed.json().get("results", listed.json())), 1)
        UserSettings.objects.create(user=self.user, show_games=False)
        hidden = APIClient().get("/api/games/?username=pepper")
        self.assertEqual(len(hidden.json().get("results", hidden.json())), 0)
        self.client.force_authenticate(self.user)
        own = self.client.get("/api/games/?username=pepper")
        self.assertEqual(len(own.json().get("results", own.json())), 1)

    def test_avatar_url_can_only_be_cleared_through_profile_update(self):
        self.client.force_authenticate(self.user)
        res = self.client.patch(
            "/api/profiles/me/", {"avatar_url": "https://evil.example/a.png"}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.content)

    def test_avatar_upload_replace_and_delete(self):
        self.client.force_authenticate(self.user)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            first = self.client.post(
                "/api/profiles/me/avatar/", {"image": _data_url(PNG)}, format="json"
            )
            self.assertEqual(first.status_code, 200, first.content)
            first_url = first.json()["avatar_url"]
            self.assertIn("/avatars/", first_url)
            self.assertTrue(first_url.endswith(".png"))

            second = self.client.post(
                "/api/profiles/me/avatar/", {"image": _data_url(JPEG, "jpeg")}, format="json"
            )
            self.assertEqual(second.status_code, 200, second.content)
            self.assertTrue(second.json()["avatar_url"].endswith(".jpg"))
            self.assertNotEqual(first_url, second.json()["avatar_url"])

            removed = self.client.delete("/api/profiles/me/avatar/")
            self.assertEqual(removed.status_code, 200, removed.content)
            self.assertEqual(removed.json()["avatar_url"], "")

    def test_avatar_rejects_bad_payloads(self):
        self.client.force_authenticate(self.user)
        too_big = _data_url(PNG + b"\x00" * (2 * 1024 * 1024))
        cases = [
            "not a data url",
            "data:image/gif;base64,R0lGODlh",
            _data_url(b"plain text, not an image"),
            _data_url(JPEG, "png"),
            too_big,
            "data:image/png;base64,@@@@",
        ]
        for image in cases:
            res = self.client.post("/api/profiles/me/avatar/", {"image": image}, format="json")
            self.assertEqual(res.status_code, 400, (image[:30], res.content))
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar_url, "")

    def test_password_change(self):
        self.client.force_authenticate(self.user)
        wrong = self.client.post(
            "/api/auth/password/change/",
            {"current_password": "nope", "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(wrong.status_code, 400, wrong.content)
        weak = self.client.post(
            "/api/auth/password/change/",
            {"current_password": PASSWORD, "new_password": "123"},
            format="json",
        )
        self.assertEqual(weak.status_code, 400, weak.content)
        self.assertIn("new_password", weak.json())

        ok = self.client.post(
            "/api/auth/password/change/",
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertIn("access", ok.json())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

        anon = APIClient().post(
            "/api/auth/password/change/",
            {"current_password": NEW_PASSWORD, "new_password": PASSWORD + "x"},
            format="json",
        )
        self.assertEqual(anon.status_code, 401)

    def test_password_change_revokes_old_refresh_tokens(self):
        anon = APIClient()
        login = anon.post(
            "/api/auth/token/", {"username": "pepper", "password": PASSWORD}, format="json"
        )
        self.assertEqual(login.status_code, 200, login.content)
        old_refresh = login.json()["refresh"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
        ok = self.client.post(
            "/api/auth/password/change/",
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        refreshed = anon.post("/api/auth/token/refresh/", {"refresh": old_refresh}, format="json")
        self.assertEqual(refreshed.status_code, 401, refreshed.content)
        fresh = anon.post(
            "/api/auth/token/refresh/", {"refresh": ok.json()["refresh"]}, format="json"
        )
        self.assertEqual(fresh.status_code, 200, fresh.content)
