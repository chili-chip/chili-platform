from __future__ import annotations

import base64
import json
import shutil
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from games.assistant import INVALID_GAME, looks_like_bitsy
from games.covers import MAX_COVER_BYTES, MAX_GAME_DATA_BYTES
from games.models import Game
from marketplace.models import Listing, Purchase

User = get_user_model()

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def png_data_url(payload: bytes = TINY_PNG) -> str:
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


SAVED_BITSY = "Sketch\n\n# BITSY VERSION 8.0\n\nROOM 0\n0000000000000000\nNAME start"
EDITED_BITSY = "Pond\n\n# BITSY VERSION 8.0\n\nROOM 0\n0000000000000000\nNAME pond\n"


class RecordingAI:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def run(self, model, options):
        self.calls.append((model, options))
        if self.error is not None:
            raise self.error
        return self.result


@contextmanager
def bound_ai(result=None, error=None):
    ai = RecordingAI(result=result, error=error)
    with patch("games.assistant.worker_env", return_value=SimpleNamespace(AI=ai)):
        yield ai


def model_json(reply: str, data: str) -> dict:
    return {"response": json.dumps({"reply": reply, "data": data})}


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

    def _release(self, user, game_id):
        self.client.force_authenticate(user)
        response = self.client.post(f"/api/games/{game_id}/release/")
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def test_anonymous_list_is_public_and_filtered_by_username(self):
        pepper = self._create(self.owner, title="Pepper game", data="pepper-data")
        self._create(self.other, title="Sage game", data="sage-data")
        self._create(self.owner, title="Draft", data="secret-draft")
        self._release(self.owner, pepper.json()["id"])
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
        self.assertEqual(game["data"], "")
        self.assertTrue(game["released"])
        self.assertEqual(game["listing_slug"], "")
        self.assertIsInstance(game["id"], int)
        self.assertIsInstance(game["created_at"], str)
        self.assertIsInstance(game["updated_at"], str)

    def test_projects_stay_private_until_release(self):
        created = self._create(self.owner, title="Sketch", data="sketch-data")
        game_id = created.json()["id"]
        self.assertFalse(created.json()["released"])
        self.assertEqual(created.json()["data"], "sketch-data")

        self.client.force_authenticate(self.owner)
        own = self.client.get("/api/games/", {"username": "pepper", "released": "false"})
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()["count"], 1)
        self.assertEqual(own.json()["results"][0]["data"], "sketch-data")
        self.assertFalse(own.json()["results"][0]["released"])

        self.client.force_authenticate(self.other)
        hidden = self.client.get("/api/games/", {"username": "pepper", "released": "false"})
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json()["count"], 0)
        self.assertEqual(self.client.get(f"/api/games/{game_id}/").status_code, 404)

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(f"/api/games/{game_id}/").status_code, 404)
        public = self.client.get("/api/games/", {"username": "pepper"})
        self.assertEqual(public.json()["count"], 0)

    def test_release_keeps_bitsy_data_and_does_not_list(self):
        created = self._create(self.owner, title="Ready", data="original-bitsy")
        game_id = created.json()["id"]
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post(f"/api/games/{game_id}/release/").status_code, 401)

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(f"/api/games/{game_id}/release/").status_code, 404)

        released = self._release(self.owner, game_id)
        body = released.json()
        self.assertTrue(body["released"])
        self.assertEqual(body["data"], "original-bitsy")
        self.assertEqual(body["listing_slug"], "")
        self.assertFalse(Listing.objects.filter(game_id=game_id).exists())
        self.assertEqual(Game.objects.get(pk=game_id).data, "original-bitsy")

        again = self._release(self.owner, game_id)
        self.assertTrue(again.json()["released"])
        self.assertEqual(again.json()["data"], "original-bitsy")
        self.assertEqual(Listing.objects.filter(game_id=game_id).count(), 0)

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(f"/api/games/{game_id}/release/").status_code, 403)
        public = self.client.get(f"/api/games/{game_id}/")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["data"], "")
        self.assertTrue(public.json()["released"])

    def test_patch_cannot_release_a_project(self):
        created = self._create(self.owner, title="Still a project", data="keep")
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/games/{game_id}/",
            {"released": True, "data": "keep"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["released"])
        self.assertEqual(response.json()["data"], "keep")
        self.assertFalse(Game.objects.get(pk=game_id).released)

    def test_buyer_can_read_bitsy_data_for_a_library_copy(self):
        created = self._create(self.owner, title="Sold", data="bitsy-sold")
        game_id = created.json()["id"]
        self._release(self.owner, game_id)
        game = Game.objects.get(pk=game_id)
        Purchase.objects.create(
            game=game,
            buyer=self.other,
            seller=self.owner,
            title=game.title,
            price_cents=100,
            status=Purchase.Status.PAID,
        )
        self.client.force_authenticate(self.other)
        owned = self.client.get(f"/api/games/{game_id}/")
        self.assertEqual(owned.status_code, 200)
        self.assertEqual(owned.json()["data"], "bitsy-sold")

        pending = Purchase.objects.create(
            game=Game.objects.create(owner=self.owner, title="Waiting", data="not-yet", released=True),
            buyer=self.other,
            seller=self.owner,
            title="Waiting",
            price_cents=100,
            status=Purchase.Status.PENDING,
        )
        waiting = self.client.get(f"/api/games/{pending.game_id}/")
        self.assertEqual(waiting.status_code, 200)
        self.assertEqual(waiting.json()["data"], "")

    def test_in_library_is_a_released_game_you_own_or_a_library_copy(self):
        created = self._create(self.owner, title="Shelf", data="bitsy-shelf")
        game_id = created.json()["id"]
        self.client.force_authenticate(self.owner)
        project = self.client.get(f"/api/games/{game_id}/")
        self.assertFalse(project.json()["in_library"])

        self._release(self.owner, game_id)
        released = self.client.get(f"/api/games/{game_id}/")
        self.assertTrue(released.json()["in_library"])

        self.client.force_authenticate(self.other)
        stranger = self.client.get(f"/api/games/{game_id}/")
        self.assertFalse(stranger.json()["in_library"])

        Purchase.objects.create(
            game=Game.objects.get(pk=game_id),
            buyer=self.other,
            seller=self.owner,
            title="Shelf",
            price_cents=100,
            status=Purchase.Status.REFUNDED,
        )
        refunded = self.client.get(f"/api/games/{game_id}/")
        self.assertTrue(refunded.json()["in_library"])

        self.client.force_authenticate(user=None)
        public = self.client.get(f"/api/games/{game_id}/")
        self.assertFalse(public.json()["in_library"])

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
            released = self.client.post(f"/api/games/{response.json()['id']}/release/")
            self.assertEqual(released.status_code, 200, released.content)
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

    def test_owner_can_read_project_data(self):
        created = self._create(self.owner, title="Open", data="secret-bitsy")
        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/games/{created.json()['id']}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], "secret-bitsy")
        self.assertEqual(response.json()["owner"], "pepper")
        self.assertFalse(response.json()["released"])

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
        self._release(self.owner, game_id)
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


class BitsyDocumentTests(TestCase):
    def test_title_and_room_count_as_a_game(self):
        self.assertTrue(looks_like_bitsy(SAVED_BITSY))
        self.assertTrue(looks_like_bitsy("  " + SAVED_BITSY.replace("\n", "\r\n")))

    def test_room_without_a_title_is_rejected(self):
        self.assertFalse(looks_like_bitsy("ROOM 0\n0000000000000000\n"))

    def test_title_without_a_room_is_rejected(self):
        self.assertFalse(looks_like_bitsy("Only a title\n\n# BITSY VERSION 8.0\n"))


class GameAssistTests(TestCase):
    def setUp(self):
        GameApiTests.setUp(self)

    def _create(self, *args, **kwargs):
        return GameApiTests._create(self, *args, **kwargs)

    def _release(self, *args, **kwargs):
        return GameApiTests._release(self, *args, **kwargs)

    def _assist(self, user, game_id, body=None):
        self.client.force_authenticate(user)
        return self.client.post(
            f"/api/games/{game_id}/assist/",
            {"message": "Add a pond."} if body is None else body,
            format="json",
        )

    def test_anonymous_and_other_users_cannot_assist(self):
        created = self._create(self.owner, title="Sketch", data=SAVED_BITSY)
        game_id = created.json()["id"]

        self.client.force_authenticate(user=None)
        anonymous = self.client.post(
            f"/api/games/{game_id}/assist/",
            {"message": "Add a pond."},
            format="json",
        )
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

        hidden = self._assist(self.other, game_id)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

        self._release(self.owner, game_id)
        forbidden = self._assist(self.other, game_id)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_assistant_is_unavailable_without_the_ai_binding(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with patch("games.assistant.worker_env", return_value=SimpleNamespace()):
            missing = self._assist(self.owner, game_id)
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.json()["detail"], "The assistant is unavailable.")

        with patch("games.assistant.worker_env", side_effect=RuntimeError("no workers")):
            offline = self._assist(self.owner, game_id)
        self.assertEqual(offline.status_code, 503)
        self.assertIn("unavailable", offline.json()["detail"].lower())
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    @patch("games.assistant.MAX_ASSIST_PROJECT_CHARS", 4)
    def test_oversize_project_is_not_sent_to_the_model(self):
        created = self._create(self.owner, data="12345")
        game_id = created.json()["id"]
        with bound_ai(result=model_json("nope", EDITED_BITSY)) as ai:
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["detail"].lower())
        self.assertIn("not changed", response.json()["detail"].lower())
        self.assertEqual(ai.calls, [])
        self.assertEqual(Game.objects.get(pk=game_id).data, "12345")

    @patch("games.assistant.MAX_PROMPT_CHARS", 10)
    def test_prompt_cap_leaves_the_game_unchanged(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result=model_json("nope", EDITED_BITSY)) as ai:
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ai.calls, [])
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_assist_returns_model_bitsy_and_does_not_write_it(self):
        created = self._create(self.owner, title="Sketch", data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result=model_json("Added a pond.", EDITED_BITSY)) as ai:
            response = self._assist(
                self.owner,
                game_id,
                {
                    "message": "Add a pond.",
                    "history": [
                        {"role": "user", "content": "make it rainy"},
                        {"role": "assistant", "content": "Added rain."},
                    ],
                    "data": "CLIENT-ONLY",
                },
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["reply"], "Added a pond.")
        self.assertEqual(body["data"], EDITED_BITSY)
        self.assertNotIn("error", body)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

        self.assertEqual(len(ai.calls), 1)
        model, options = ai.calls[0]
        self.assertEqual(model, "@cf/qwen/qwen2.5-coder-32b-instruct")
        messages = options["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("ROOM", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "make it rainy")
        self.assertEqual(messages[2]["content"], "Added rain.")
        self.assertIn(SAVED_BITSY, messages[-1]["content"])
        self.assertIn("Add a pond.", messages[-1]["content"])
        self.assertNotIn("CLIENT-ONLY", json.dumps(messages))

    @override_settings(ASSISTANT_MODEL="@cf/test/model")
    def test_model_id_comes_from_the_worker_var(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        with bound_ai(result=model_json("Added a pond.", EDITED_BITSY)) as ai:
            response = self._assist(self.owner, created.json()["id"])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ai.calls[0][0], "@cf/test/model")

    def test_fenced_model_json_is_accepted(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        fenced = "```json\n" + json.dumps({"reply": "Added a pond.", "data": EDITED_BITSY}) + "\n```"
        with bound_ai(result={"response": fenced}):
            response = self._assist(self.owner, created.json()["id"])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"], EDITED_BITSY)
        self.assertEqual(Game.objects.get(pk=created.json()["id"]).data, SAVED_BITSY)

    def test_invalid_model_game_returns_the_reply_and_no_data(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result=model_json("I could not draw that.", "Just a title\n")):
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["reply"], "I could not draw that.")
        self.assertEqual(body["error"], INVALID_GAME)
        self.assertNotIn("data", body)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_unparseable_model_output_is_not_applied(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result={"response": "not json"}):
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("data", response.json())
        self.assertEqual(response.json()["error"], INVALID_GAME)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_model_failure_leaves_the_game_unchanged(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(error=RuntimeError("workers ai down")):
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 502)
        self.assertIn("not changed", response.json()["detail"].lower())
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_blank_message_is_rejected(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        response = self._assist(self.owner, created.json()["id"], {"message": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.json())
        self.assertEqual(Game.objects.get(pk=created.json()["id"]).data, SAVED_BITSY)
