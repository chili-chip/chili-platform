"""Throttle and storage caps for public writes."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from community.models import FORUM_BODY_MAX_LENGTH, FORUM_TITLE_MAX_LENGTH, ForumCategory, ForumPost
from games.models import MAX_PROJECTS_PER_USER, Game

User = get_user_model()
PASSWORD = "harbor-lantern-57"


def _tight_framework():
    current = settings.REST_FRAMEWORK
    rates = {scope: "2/minute" for scope in current["DEFAULT_THROTTLE_RATES"]}
    return {**current, "DEFAULT_THROTTLE_RATES": rates}


class AbuseLimitTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password=PASSWORD,
            email_verified=True,
        )
        self.other = User.objects.create_user(
            username="sage",
            email="sage@chili.example",
            password=PASSWORD,
            email_verified=True,
        )
        self.category = ForumCategory.objects.create(name="General", slug="general")

    def test_published_limits(self):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertNotIn("DEFAULT_THROTTLE_CLASSES", settings.REST_FRAMEWORK)
        self.assertEqual(
            rates,
            {
                "auth_register": "30/hour",
                "auth_token": "10/minute",
                "auth_refresh": "30/minute",
                "auth_social": "30/minute",
                "account_write": "60/hour",
                "password_change": "5/hour",
                "email_change": "5/hour",
                "forum_write": "60/hour",
                "game_write": "240/minute",
                "listing_write": "30/hour",
                "store_checkout": "10/hour",
                "marketplace_checkout": "10/hour",
                "newsletter_subscribe": "10/hour",
            },
        )
        self.assertEqual(MAX_PROJECTS_PER_USER, 30)
        self.assertEqual(FORUM_TITLE_MAX_LENGTH, 160)
        self.assertEqual(FORUM_BODY_MAX_LENGTH, 4000)

    def test_auth_writes_are_throttled_per_ip(self):
        with override_settings(REST_FRAMEWORK=_tight_framework()):
            for path in ("/api/auth/register/", "/api/auth/token/", "/api/auth/token/refresh/"):
                self._assert_throttled("post", path, ip="203.0.113.10")
                follow_up = self.client.post(
                    path,
                    {},
                    format="json",
                    HTTP_CF_CONNECTING_IP="203.0.113.11",
                )
                self.assertNotEqual(follow_up.status_code, 429, path)

    def test_signed_in_writes_are_throttled_per_user(self):
        with override_settings(REST_FRAMEWORK=_tight_framework()):
            self.client.force_authenticate(self.user)
            cases = (
                ("post", "/api/forum/posts/", {"title": "Hi", "content": "Hello there", "category": self.category.id}),
                ("post", "/api/games/", {"title": "Sketch", "data": "room 0"}),
                ("post", "/api/marketplace/listings/", {}),
                ("post", "/api/store/checkout/", {}),
                ("post", "/api/marketplace/listings/missing/checkout/", {}),
            )
            for method, path, payload in cases:
                self._assert_throttled(method, path, payload, user=self.user)

            post = ForumPost.objects.create(
                title="Already here",
                content="A short post.",
                author=self.user,
                category=self.category,
            )
            self._assert_throttled(
                "post",
                "/api/forum/comments/",
                {"post": post.id, "content": "A comment."},
                user=self.user,
            )
            self._assert_throttled(
                "post",
                f"/api/forum/posts/{post.id}/comments/",
                {"content": "Another comment."},
                user=self.user,
            )

            self.client.force_authenticate(self.other)
            other = self.client.post(
                "/api/forum/posts/",
                {"title": "Sage", "content": "Still open.", "category": self.category.id},
                format="json",
            )
            self.assertNotEqual(other.status_code, 429, other.content)

    def test_authenticated_reads_do_not_use_the_write_budget(self):
        with override_settings(REST_FRAMEWORK=_tight_framework()):
            self.client.force_authenticate(self.user)
            for _ in range(5):
                listed = self.client.get("/api/games/")
                self.assertEqual(listed.status_code, 200)
                posts = self.client.get("/api/forum/posts/")
                self.assertEqual(posts.status_code, 200)
            created = self.client.post(
                "/api/games/",
                {"title": "After reads", "data": "room"},
                format="json",
            )
            self.assertEqual(created.status_code, 201, created.content)

    def test_forum_title_and_body_are_capped(self):
        self.client.force_authenticate(self.user)
        title = "T" * FORUM_TITLE_MAX_LENGTH
        body = "b" * FORUM_BODY_MAX_LENGTH
        created = self.client.post(
            "/api/forum/posts/",
            {"title": title, "content": body, "category": self.category.id},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        post_id = created.json()["id"]

        long_title = self.client.post(
            "/api/forum/posts/",
            {"title": title + "x", "content": "Hello there", "category": self.category.id},
            format="json",
        )
        self.assertEqual(long_title.status_code, 400, long_title.content)
        self.assertIn("160", str(long_title.json()))

        long_body = self.client.post(
            "/api/forum/posts/",
            {"title": "Too long", "content": body + "x", "category": self.category.id},
            format="json",
        )
        self.assertEqual(long_body.status_code, 400, long_body.content)
        self.assertIn("4000", str(long_body.json()))

        comment = self.client.post(
            "/api/forum/comments/",
            {"post": post_id, "content": body},
            format="json",
        )
        self.assertEqual(comment.status_code, 201, comment.content)
        nested = self.client.post(
            f"/api/forum/posts/{post_id}/comments/",
            {"content": body + "x"},
            format="json",
        )
        self.assertEqual(nested.status_code, 400, nested.content)
        self.assertIn("4000", str(nested.json()))

    def test_project_cap_blocks_another_save_and_still_allows_updates(self):
        Game.objects.bulk_create(
            [
                Game(owner=self.user, title=f"Game {index}", slug=f"game-{index}", data="x")
                for index in range(MAX_PROJECTS_PER_USER)
            ]
        )
        self.client.force_authenticate(self.user)
        blocked = self.client.post(
            "/api/games/",
            {"title": "One more", "data": "room"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertEqual(
            blocked.json()["detail"],
            "You can keep 30 projects. Delete one before saving another.",
        )

        existing = Game.objects.filter(owner=self.user).order_by("id").first()
        updated = self.client.put(
            f"/api/games/{existing.id}/",
            {"title": "Renamed", "data": "still here"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["title"], "Renamed")

        deleted = self.client.delete(f"/api/games/{existing.id}/")
        self.assertEqual(deleted.status_code, 204, deleted.content)
        opened = self.client.post(
            "/api/games/",
            {"title": "After delete", "data": "room"},
            format="json",
        )
        self.assertEqual(opened.status_code, 201, opened.content)
        self.assertEqual(Game.objects.filter(owner=self.user).count(), MAX_PROJECTS_PER_USER)

    def test_refund_stays_staff_only(self):
        self.client.force_authenticate(self.user)
        denied = self.client.post("/api/marketplace/purchases/1/refund/", format="json")
        self.assertEqual(denied.status_code, 403, denied.content)
        self.client.force_authenticate(user=None)
        anonymous = self.client.post("/api/marketplace/purchases/1/refund/", format="json")
        self.assertEqual(anonymous.status_code, 401, anonymous.content)

    def _assert_throttled(self, method, path, payload=None, user=None, ip="203.0.113.10"):
        cache.clear()
        if user is None:
            self.client.force_authenticate(user=None)
        else:
            self.client.force_authenticate(user)
        request = getattr(self.client, method)
        last = None
        for index in range(3):
            last = request(
                path,
                {} if payload is None else payload,
                format="json",
                HTTP_CF_CONNECTING_IP=ip,
            )
            if index < 2:
                self.assertNotEqual(last.status_code, 429, (path, last.content))
        self.assertEqual(last.status_code, 429, (path, last.content))
        self.assertIn("throttled", last.json()["detail"].lower())
