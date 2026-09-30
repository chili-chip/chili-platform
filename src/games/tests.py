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

from games.assistant import CANNOT_MERGE, iter_assist_events, looks_like_bitsy
from games.bitsy import merge_change
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


def _map_rows() -> str:
    return "\n".join(["0" * 16] * 16)


def _room_block(name: str = "start", room_id: str = "0") -> str:
    return f"ROOM {room_id}\n{_map_rows()}\nNAME {name}"


def _game_text(title: str = "Sketch", room_name: str = "start") -> str:
    return f"{title}\n\n# BITSY VERSION 8.0\n\n! ROOM_FORMAT 0\n\n{_room_block(room_name)}"


SAVED_BITSY = _game_text()
POND_BLOCK = _room_block("pond")


class RecordingAI:
    def __init__(self, result=None, error=None, coroutine=False):
        self.result = result
        self.error = error
        self.coroutine = coroutine
        self.calls = []
        self.awaited = False

    def run(self, model, options):
        self.calls.append((model, options))
        if not self.coroutine:
            if self.error is not None:
                raise self.error
            return self.result
        # workers.env.AI.run returns a coroutine. The JS promise starts when
        # the method is called; the view has to await that coroutine.
        error = self.error
        result = self.result
        owner = self

        async def finish():
            owner.awaited = True
            if error is not None:
                raise error
            return result

        return finish()


@contextmanager
def bound_ai(result=None, error=None, coroutine=False):
    ai = RecordingAI(result=result, error=error, coroutine=coroutine)
    with patch("games.assistant.worker_env", return_value=SimpleNamespace(AI=ai)):
        yield ai


def model_change(reply: str, kind: str, block_id: str, block: str) -> dict:
    text = f"REPLY: {reply}\nKIND: {kind}\nID: {block_id}\nBLOCK:\n{block}\n"
    return {"response": text}


def assist_events(response) -> list[dict]:
    raw = b"".join(response.streaming_content)
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


class _ChunkReader:
    def __init__(self, parts: list[str]):
        self.parts = list(parts)

    def read(self):
        parts = self.parts

        async def once():
            if not parts:
                return {"done": True, "value": None}
            return {"done": False, "value": parts.pop(0)}

        return once()


class _ChunkStream:
    def __init__(self, parts: list[str]):
        self.parts = parts

    def getReader(self):
        return _ChunkReader(self.parts)


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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
    def test_editor_sized_room_parses(self):
        self.assertTrue(looks_like_bitsy(SAVED_BITSY))
        self.assertTrue(looks_like_bitsy(SAVED_BITSY.replace("\n", "\r\n")))

    def test_short_room_map_does_not_parse(self):
        short = "Sketch\n\nROOM 0\n0000000000000000\nNAME start\n"
        self.assertFalse(looks_like_bitsy(short))

    def test_title_without_a_room_is_rejected(self):
        self.assertFalse(looks_like_bitsy("Only a title\n\n# BITSY VERSION 8.0\n"))

    def test_room_merge_keeps_the_rest_of_the_game(self):
        source = SAVED_BITSY + "\n\nPAL 1\n0,0,0\n255,255,255\n0,255,0\nNAME day\n"
        merged = merge_change(source, "room", "0", POND_BLOCK)
        self.assertIsNotNone(merged)
        self.assertIn("NAME pond", merged)
        self.assertNotIn("NAME start", merged)
        self.assertIn("PAL 1", merged)
        self.assertIn("Sketch", merged)
        self.assertTrue(looks_like_bitsy(merged))

    def test_new_dialog_is_appended(self):
        merged = merge_change(SAVED_BITSY, "dialog", "1", 'DLG 1\nHello pond\n')
        self.assertIsNotNone(merged)
        self.assertIn("DLG 1\nHello pond", merged)
        self.assertIn("ROOM 0", merged)

    def test_two_blocks_are_not_merged(self):
        block = POND_BLOCK + "\n\nDLG 1\nHello\n"
        self.assertIsNone(merge_change(SAVED_BITSY, "room", "0", block))

    def test_sprite_placement_without_a_sprite_does_not_parse(self):
        broken = _game_text().replace("NAME start", "NAME start\nSPR A 1,2")
        self.assertFalse(looks_like_bitsy(broken))

    def test_unclosed_quote_does_not_parse(self):
        self.assertFalse(looks_like_bitsy('"""\nSketch\n\nROOM 0\n' + _map_rows() + "\n"))

    def test_short_drawing_does_not_parse(self):
        self.assertFalse(looks_like_bitsy(_game_text() + "\n\nSPR A\n00000000\n"))


class GameAssistTests(TestCase):
    def setUp(self):
        GameApiTests.setUp(self)

    def _create(self, *args, **kwargs):
        return GameApiTests._create(self, *args, **kwargs)

    def _release(self, *args, **kwargs):
        return GameApiTests._release(self, *args, **kwargs)

    def _assist(self, user, game_id, body=None):
        self.client.force_authenticate(user)
        payload = {"message": "Add a pond.", "data": SAVED_BITSY}
        if body is not None:
            payload.update(body)
        return self.client.post(
            f"/api/games/{game_id}/assist/",
            payload,
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

        with override_settings(ON_WORKERS=True):
            with patch("games.assistant.worker_env", return_value=SimpleNamespace()):
                local_worker = self._assist(self.owner, game_id)
        self.assertEqual(local_worker.status_code, 503)
        detail = local_worker.json()["detail"]
        self.assertIn("unavailable", detail.lower())
        self.assertIn("wrangler login", detail)
        self.assertIn("npm run dev:ai", detail)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

        with patch("games.assistant.worker_env", side_effect=RuntimeError("no workers")):
            offline = self._assist(self.owner, game_id)
        self.assertEqual(offline.status_code, 503)
        self.assertIn("unavailable", offline.json()["detail"].lower())
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

        self.client.force_authenticate(self.owner)
        with patch("games.assistant.worker_env", return_value=SimpleNamespace()):
            browser = self.client.post(
                f"/api/games/{game_id}/assist/",
                {"message": "Add a pond.", "data": SAVED_BITSY},
                format="json",
                HTTP_ACCEPT="application/x-ndjson",
            )
        self.assertNotEqual(browser.status_code, 406)
        self.assertEqual(browser.status_code, 503)
        self.assertIn("unavailable", json.loads(browser.content)["detail"].lower())
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    @patch("games.assistant.MAX_ASSIST_PROJECT_CHARS", 4)
    def test_oversize_project_is_not_sent_to_the_model(self):
        created = self._create(self.owner, data="12345")
        game_id = created.json()["id"]
        with bound_ai(result=model_change("nope", "room", "0", POND_BLOCK)) as ai:
            response = self._assist(self.owner, game_id, {"data": "12345"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["detail"].lower())
        self.assertIn("not changed", response.json()["detail"].lower())
        self.assertEqual(ai.calls, [])
        self.assertEqual(Game.objects.get(pk=game_id).data, "12345")

    @patch("games.assistant.MAX_PROMPT_CHARS", 10)
    def test_prompt_cap_leaves_the_game_unchanged(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result=model_change("nope", "room", "0", POND_BLOCK)) as ai:
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ai.calls, [])
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_assist_merges_one_room_from_the_editor_text(self):
        created = self._create(self.owner, title="Sketch", data=SAVED_BITSY)
        game_id = created.json()["id"]
        on_screen = _game_text("Meadow")
        with bound_ai(result=model_change("Added a pond.", "room", "0", POND_BLOCK)) as ai:
            response = self._assist(
                self.owner,
                game_id,
                {
                    "message": "Add a pond.",
                    "history": [
                        {"role": "user", "content": "make it rainy"},
                        {"role": "assistant", "content": "Added rain."},
                    ],
                    "data": on_screen,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/x-ndjson", response["Content-Type"])
        events = assist_events(response)
        self.assertGreaterEqual(len(events), 2)
        self.assertIn("reply", events[0])
        self.assertNotIn("data", events[0])
        self.assertTrue(events[0]["reply"])
        self.assertTrue(events[-1]["reply"].startswith(events[0]["reply"]))
        body = events[-1]
        self.assertEqual(body["reply"], "Added a pond.")
        self.assertNotIn("error", body)
        self.assertIn("Meadow", body["data"])
        self.assertIn("NAME pond", body["data"])
        self.assertNotIn("NAME start", body["data"])
        self.assertNotEqual(body["data"], on_screen)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

        self.assertEqual(len(ai.calls), 1)
        model, options = ai.calls[0]
        self.assertEqual(model, "@cf/qwen/qwen2.5-coder-32b-instruct")
        self.assertTrue(options["stream"])
        messages = options["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("ROOM", messages[0]["content"])
        self.assertIn("Do not rewrite the whole game.", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "make it rainy")
        self.assertEqual(messages[2]["content"], "Added rain.")
        self.assertIn(on_screen, messages[-1]["content"])
        self.assertIn("Add a pond.", messages[-1]["content"])
        self.assertNotIn("Sketch\n\n# BITSY", messages[-1]["content"])

    @override_settings(ASSISTANT_MODEL="@cf/test/model")
    def test_model_id_comes_from_the_worker_var(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        with bound_ai(result=model_change("Added a pond.", "room", "0", POND_BLOCK)) as ai:
            response = self._assist(self.owner, created.json()["id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ai.calls[0][0], "@cf/test/model")

    def test_fenced_block_json_is_merged(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        fenced = (
            "```json\n"
            + json.dumps(
                {"reply": "Added a pond.", "kind": "room", "id": "0", "block": POND_BLOCK}
            )
            + "\n```"
        )
        with bound_ai(result={"response": fenced}):
            response = self._assist(self.owner, created.json()["id"])
        self.assertEqual(response.status_code, 200)
        body = assist_events(response)[-1]
        self.assertIn("NAME pond", body["data"])
        self.assertEqual(Game.objects.get(pk=created.json()["id"]).data, SAVED_BITSY)

    def test_full_document_rewrite_is_not_applied(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        rewritten = json.dumps({"reply": "Rewrote everything.", "data": _game_text("Pond", "pond")})
        with bound_ai(result={"response": rewritten}):
            response = self._assist(self.owner, game_id)
        body = assist_events(response)[-1]
        self.assertEqual(body["reply"], "Rewrote everything.")
        self.assertEqual(body["error"], CANNOT_MERGE)
        self.assertNotIn("data", body)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_invalid_block_returns_the_reply_and_no_data(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        short = "ROOM 0\n0000000000000000\nNAME pond"
        with bound_ai(result=model_change("I could not draw that.", "room", "0", short)):
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 200)
        body = assist_events(response)[-1]
        self.assertEqual(body["reply"], "I could not draw that.")
        self.assertEqual(body["error"], CANNOT_MERGE)
        self.assertNotIn("data", body)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_unparseable_model_output_is_not_applied(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result={"response": "not json"}):
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 200)
        body = assist_events(response)[-1]
        self.assertNotIn("data", body)
        self.assertEqual(body["error"], CANNOT_MERGE)
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_reply_tokens_arrive_before_the_merged_game(self):
        first = 'data: {"response":"REPLY: Added"}\n\n'
        rest = " a pond.\nKIND: room\nID: 0\nBLOCK:\n" + POND_BLOCK + "\n"
        second = "data: " + json.dumps({"response": rest}) + "\n\n"
        events = list(iter_assist_events(SAVED_BITSY, _ChunkStream([first, second, "data: [DONE]\n\n"])))
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["reply"], "Added")
        self.assertNotIn("data", events[0])
        self.assertEqual(events[-1]["reply"], "Added a pond.")
        self.assertIn("NAME pond", events[-1]["data"])

    def test_split_sse_chunk_still_merges_after_the_reply(self):
        text = "REPLY: Added a pond.\nKIND: room\nID: 0\nBLOCK:\n" + POND_BLOCK + "\n"
        raw = "data: " + json.dumps({"response": text}) + "\n\n"
        events = list(iter_assist_events(SAVED_BITSY, _ChunkStream([raw[:2], raw[2:]])))
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["reply"], "Added a pond.")
        self.assertNotIn("data", events[0])
        self.assertIn("NAME pond", events[-1]["data"])

    def test_binding_coroutine_is_awaited_and_not_a_gateway_error(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result=model_change("Added a pond.", "room", "0", POND_BLOCK), coroutine=True) as ai:
            response = self._assist(self.owner, game_id)
        self.assertTrue(ai.awaited)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/x-ndjson", response["Content-Type"])
        body = assist_events(response)[-1]
        self.assertIn("NAME pond", body["data"])
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)
        self.assertIsInstance(ai.calls[0][1], dict)
        self.assertIn("messages", ai.calls[0][1])

    def test_rejected_binding_coroutine_is_json_and_leaves_the_game(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(error=RuntimeError("workers ai rejected"), coroutine=True) as ai:
            response = self._assist(self.owner, game_id)
        self.assertTrue(ai.awaited)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("not changed", response.json()["detail"].lower())
        self.assertNotIn("data", response.json())
        self.assertEqual(Game.objects.get(pk=game_id).data, SAVED_BITSY)

    def test_invalid_binding_coroutine_is_not_applied(self):
        created = self._create(self.owner, data=SAVED_BITSY)
        game_id = created.json()["id"]
        with bound_ai(result=model_change("I could not draw that.", "room", "0", "ROOM 0\n0\n"), coroutine=True):
            response = self._assist(self.owner, game_id)
        self.assertEqual(response.status_code, 200)
        body = assist_events(response)[-1]
        self.assertNotIn("data", body)
        self.assertEqual(body["error"], CANNOT_MERGE)
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
