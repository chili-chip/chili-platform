"""Markdown editor on long-text fields in the admin."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from app.admin_markdown import EDITOR_CSS, FONT_AWESOME_CSS
from community.models import ForumCategory, ForumComment, ForumPost

User = get_user_model()

EDITOR_CLASS = "easymde-box"
EDITOR_JS = "easymde/easymde.min.js"


class AdminMarkdownEditorTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="chief",
            email="chief@chili.example",
            password="harbor-lantern-57",
        )
        self.client.force_login(self.admin)

    def _add_page(self, model_admin_url: str) -> str:
        response = self.client.get(reverse(model_admin_url))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_long_text_fields_use_the_editor(self):
        cases = {
            "admin:community_forumpost_add": 'name="content"',
            "admin:community_forumcomment_add": 'name="content"',
            "admin:marketplace_listing_add": 'name="description"',
            "admin:store_product_add": 'name="long_description"',
        }
        for url, field in cases.items():
            with self.subTest(url=url):
                html = self._add_page(url)
                textarea = html[html.index(field) - 300 : html.index(field) + 300]
                self.assertIn(EDITOR_CLASS, textarea)
                self.assertIn(EDITOR_JS, html)
                self.assertIn(FONT_AWESOME_CSS, html)

    def test_other_text_fields_stay_plain(self):
        # Bitsy game source is not markdown.
        html = self._add_page("admin:games_game_add")
        self.assertNotIn(EDITOR_CLASS, html)
        self.assertNotIn(EDITOR_JS, html)

    def test_editor_assets_are_collected(self):
        for path in (
            EDITOR_JS,
            "easymde/easymde.init.js",
            "easymde/easymde.min.css",
            FONT_AWESOME_CSS,
            EDITOR_CSS,
            "admin-markdown/font-awesome/fonts/fontawesome-webfont.woff2",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path))

    def test_saving_keeps_markdown_source(self):
        category = ForumCategory.objects.create(name="General", slug="general")
        post = ForumPost.objects.create(
            title="Hello", slug="hello", content="hi", author=self.admin, category=category
        )
        body = "## Patch notes\n\n- **faster** saves\n- [docs](https://chilichip.eu)"
        response = self.client.post(
            reverse("admin:community_forumcomment_add"),
            {"post": post.pk, "author": self.admin.pk, "content": body},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ForumComment.objects.get().content, body)
