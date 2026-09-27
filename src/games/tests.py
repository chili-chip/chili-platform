from __future__ import annotations

import base64
import shutil
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from games.covers import MAX_COVER_BYTES, MAX_GAME_DATA_BYTES
from games.models import Game

User = get_user_model()

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def png_data_url(payload: bytes = TINY_PNG) -> str:
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


class GameApiTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))
        self.media_settings = override_settings(MEDIA_ROOT=self.media_root)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)

        self.client = APIClient()
        self.owner = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password="supersecret",
        )
        self.other = User.objects.create_user(
            username="sage",
            email="sage@chili.example",
            password="supersecret",
        )
        self.staff = User.objects.create_user(
            username="admin",
            email="admin@chili.example",
            password="supersecret",
            is_staff=True,
        )

    def _create(self, user, title="untitled", data="room 0", **extra):
        self.client.force_authenticate(user)
        response = self.client.post(
            "/api/games/",
            {"title": title, "data": data, **extra},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response

    def test_anonymous_list_is_public_and_filtered_by_username(self):
        self._create(self.owner, title="Pepper game", data="pepper-data")
        self._create(self.other, title="Sage game", data="sage-data")
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/games/", {"username": "pepper"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertIsNone(body["next"])
        self.assertIsNone(body["previous"])
        game = body["results"][0]
        self.assertEqual(game["title"], "Pepper game")
        self.assertEqual(game["owner"], "pepper")
        self.assertEqual(game["slug"], "pepper-game")
        self.assertEqual(game["cover"], "")
        self.assertEqual(game["data"], "pepper-data")
        self.assertIsInstance(game["id"], int)
        self.assertIsInstance(game["created_at"], str)
        self.assertIsInstance(game["updated_at"], str)

    def test_unknown_username_returns_an_empty_page(self):
        self._create(self.owner)
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/games/", {"username": "nobody"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])
        self.assertEqual(response.json()["count"], 0)

    def test_list_is_newest_first_and_paginated(self):
        self.client.force_authenticate(self.owner)
        for index in range(21):
            response = self.client.post(
                "/api/games/",
                {"title": f"Game {index}", "data": f"data {index}"},
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.content)
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/games/", {"username": "pepper"})
        body = response.json()
        self.assertEqual(body["count"], 21)
        self.assertEqual(len(body["results"]), 20)
        self.assertEqual(body["results"][0]["title"], "Game 20")
        self.assertIn("username=pepper", body["next"])

    def test_create_requires_auth_and_ignores_owner_override(self):
        response = self.client.post(
            "/api/games/",
            {"title": "Nope", "data": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

        created = self._create(self.owner, title="Mine", data="bitsy", owner="admin")
        self.assertEqual(created.json()["owner"], "pepper")
        self.assertEqual(Game.objects.get(pk=created.json()["id"]).owner_id, self.owner.id)

    def test_duplicate_titles_get_unique_slugs(self):
        first = self._create(self.owner, title="Hello, World!")
        second = self._create(self.owner, title="Hello, World!")
        self.assertEqual(first.json()["slug"], "hello-world")
        self.assertEqual(second.json()["slug"], "hello-world-2")

    def test_blank_title_is_rejected(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/games/",
            {"title": "   ", "data": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json())

    def test_anonymous_can_read_game_data(self):
        created = self._create(self.owner, title="Open", data="secret-bitsy")
        self.client.force_authenticate(user=None)
        response = self.client.get(f"/api/games/{created.json()['id']}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], "secret-bitsy")
        self.assertEqual(response.json()["owner"], "pepper")

    def test_missing_and_non_numeric_ids_are_not_found(self):
        self.assertEqual(self.client.get("/api/games/999/").status_code, 404)
        self.assertEqual(self.client.get("/api/games/nope/").status_code, 404)

    def test_owner_put_updates_title_data_and_slug_without_clearing_cover(self):
        created = self._create(self.owner, title="First", data="old")
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        patched = self.client.patch(
            f"/api/games/{game_id}/",
            {"cover": png_data_url()},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        cover = patched.json()["cover"]

        replaced = self.client.put(
            f"/api/games/{game_id}/",
            {"title": "Second", "data": "new"},
            format="json",
        )
        self.assertEqual(replaced.status_code, 200, replaced.content)
        body = replaced.json()
        self.assertEqual(body["title"], "Second")
        self.assertEqual(body["data"], "new")
        self.assertEqual(body["slug"], "second")
        self.assertEqual(body["cover"], cover)

    def test_other_user_and_staff_cannot_change_or_delete(self):
        created = self._create(self.owner, title="Owned", data="keep")
        game_id = created.json()["id"]
        for user in (self.other, self.staff):
            self.client.force_authenticate(user)
            for method, payload in (
                ("put", {"title": "Stolen", "data": "nope"}),
                ("patch", {"title": "Stolen"}),
                ("delete", None),
            ):
                request = getattr(self.client, method)
                kwargs = {"format": "json"}
                if payload is not None:
                    kwargs["data"] = payload
                response = request(f"/api/games/{game_id}/", **kwargs)
                self.assertEqual(response.status_code, 403, response.content)
        self.assertTrue(Game.objects.filter(pk=game_id, title="Owned").exists())

    def test_anonymous_cannot_update_or_delete(self):
        created = self._create(self.owner)
        game_id = created.json()["id"]
        self.client.force_authenticate(user=None)
        self.assertEqual(
            self.client.put(
                f"/api/games/{game_id}/",
                {"title": "X", "data": "y"},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(self.client.patch(f"/api/games/{game_id}/", {"title": "X"}, format="json").status_code, 401)
        self.assertEqual(self.client.delete(f"/api/games/{game_id}/").status_code, 401)

    def test_patch_cover_stores_png_in_media_and_not_the_data_url(self):
        created = self._create(self.owner, title="Covered", data="bitsy-source")
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/games/{game_id}/",
            {"cover": png_data_url()},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["title"], "Covered")
        self.assertEqual(body["data"], "bitsy-source")
        self.assertTrue(body["cover"].startswith("http://testserver/media/games/covers/"))
        self.assertNotIn("data:", body["cover"])
        self.assertNotIn("base64", body["cover"])

        game = Game.objects.get(pk=game_id)
        self.assertTrue(game.cover.name.startswith(f"games/covers/{game.pk}/"))
        self.assertTrue(game.cover.name.endswith(".png"))
        self.assertNotIn("data:", game.cover.name)
        with game.cover.open("rb") as handle:
            self.assertEqual(handle.read(), TINY_PNG)

        stored = Game.objects.values_list("cover", flat=True).get(pk=game_id)
        self.assertEqual(stored, game.cover.name)
        self.assertNotIn("data:", stored)

        media = self.client.get(urlparse(body["cover"]).path)
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media["Content-Type"], "image/png")
        self.assertEqual(b"".join(media.streaming_content), TINY_PNG)

    def test_replacing_cover_deletes_the_previous_file(self):
        created = self._create(self.owner)
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        first = self.client.patch(
            f"/api/games/{game_id}/",
            {"cover": png_data_url()},
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.content)
        previous = Game.objects.get(pk=game_id).cover.name
        self.assertTrue(default_storage.exists(previous))

        second_png = TINY_PNG + b"\x00"
        # Keep a valid PNG signature; extra trailing byte is still accepted as PNG bytes.
        second = self.client.patch(
            f"/api/games/{game_id}/",
            {"cover": png_data_url(second_png)},
            format="json",
        )
        self.assertEqual(second.status_code, 200, second.content)
        current = Game.objects.get(pk=game_id).cover.name
        self.assertNotEqual(current, previous)
        self.assertFalse(default_storage.exists(previous))
        self.assertTrue(default_storage.exists(current))
        with Game.objects.get(pk=game_id).cover.open("rb") as handle:
            self.assertEqual(handle.read(), second_png)

    def test_empty_cover_clears_the_file(self):
        created = self._create(self.owner)
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        self.client.patch(f"/api/games/{game_id}/", {"cover": png_data_url()}, format="json")
        previous = Game.objects.get(pk=game_id).cover.name
        cleared = self.client.patch(f"/api/games/{game_id}/", {"cover": ""}, format="json")
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertEqual(cleared.json()["cover"], "")
        self.assertFalse(Game.objects.get(pk=game_id).cover)
        self.assertFalse(default_storage.exists(previous))

    def test_invalid_cover_is_rejected_and_keeps_the_existing_file(self):
        created = self._create(self.owner, title="Stay", data="same")
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        saved = self.client.patch(
            f"/api/games/{game_id}/",
            {"cover": png_data_url()},
            format="json",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        previous = saved.json()["cover"]
        previous_name = Game.objects.get(pk=game_id).cover.name

        rejected = (
            "data:image/jpeg;base64," + base64.b64encode(TINY_PNG).decode("ascii"),
            "data:image/png;base64," + base64.b64encode(b"not a png").decode("ascii"),
            "data:image/png;base64,@@@@",
            "https://example.test/cover.png",
        )
        for cover in rejected:
            response = self.client.patch(
                f"/api/games/{game_id}/",
                {"cover": cover},
                format="json",
            )
            self.assertEqual(response.status_code, 400, cover)
            self.assertIn("cover", response.json())
            self.assertEqual(Game.objects.get(pk=game_id).cover.name, previous_name)
            self.assertTrue(default_storage.exists(previous_name))

        current = self.client.get(f"/api/games/{game_id}/").json()
        self.assertEqual(current["cover"], previous)
        self.assertEqual(current["title"], "Stay")
        self.assertEqual(current["data"], "same")

    @patch("games.covers.MAX_COVER_BYTES", 8)
    def test_oversize_cover_is_rejected(self):
        self.assertLess(8, len(TINY_PNG))
        self.assertLess(8, MAX_COVER_BYTES)
        created = self._create(self.owner)
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/games/{created.json()['id']}/",
            {"cover": png_data_url()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("cover", response.json())
        self.assertFalse(Game.objects.get(pk=created.json()["id"]).cover)

    @patch("games.serializers.MAX_GAME_DATA_BYTES", 4)
    def test_oversize_data_is_rejected(self):
        self.assertLess(4, MAX_GAME_DATA_BYTES)
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/games/",
            {"title": "Big", "data": "12345"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("data", response.json())
        self.assertFalse(Game.objects.filter(title="Big").exists())

    def test_owner_delete_removes_the_game_and_cover_file(self):
        created = self._create(self.owner)
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        self.client.patch(f"/api/games/{game_id}/", {"cover": png_data_url()}, format="json")
        cover_name = Game.objects.get(pk=game_id).cover.name
        self.assertTrue(default_storage.exists(cover_name))

        deleted = self.client.delete(f"/api/games/{game_id}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Game.objects.filter(pk=game_id).exists())
        self.assertFalse(default_storage.exists(cover_name))
        self.assertEqual(self.client.get(f"/api/games/{game_id}/").status_code, 404)
