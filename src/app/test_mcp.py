from __future__ import annotations

import json

from django.test import Client, TestCase, override_settings

from community.mcp import CommunityDevWriteTools


@override_settings(MCP_ENABLED=True)
class McpEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def _post_mcp(self, body: dict):
        return self.client.post(
            "/mcp",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_ACCEPT="application/json, text/event-stream",
        )

    def _initialize(self) -> None:
        response = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "chili-test", "version": "0.1.0"},
                },
            }
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_tools_list_includes_platform_and_query_tools(self) -> None:
        self._initialize()
        response = self._post_mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        tools = (payload.get("result") or {}).get("tools") or []
        names = {tool["name"] for tool in tools}
        self.assertIn("platform_health", names)
        self.assertIn("query_data_collections", names)
        self.assertIn("get_server_instructions", names)
        self.assertIn("create_forum_post", names)

    def test_create_forum_post_tool(self) -> None:
        from django.contrib.auth import get_user_model

        from community.models import ForumCategory, ForumPost

        User = get_user_model()
        User.objects.create_user(username="admin", email="admin@localhost", password="x")
        ForumCategory.objects.create(name="General", slug="general", description="Talk")
        self._initialize()
        response = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "create_forum_post",
                    "arguments": {
                        "title": "MCP test post",
                        "content": "Created by MCP smoke test.",
                        "category_slug": "general",
                    },
                },
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        content = (payload.get("result") or {}).get("content") or []
        text_block = next(item for item in content if item.get("type") == "text")
        created = json.loads(text_block["text"])
        self.assertEqual(created["title"], "MCP test post")
        post = ForumPost.objects.get(pk=created["id"])
        self.assertEqual(post.slug, created["slug"])
        self.assertEqual(created["community_path"], f"/community/post/{post.id}")

    def test_create_forum_post_rejected_when_mcp_disabled(self) -> None:
        from django.contrib.auth import get_user_model

        from community.models import ForumCategory

        User = get_user_model()
        User.objects.create_user(username="admin", email="admin@localhost", password="x")
        ForumCategory.objects.create(name="General", slug="general", description="Talk")
        with self.settings(MCP_ENABLED=False):
            tools = CommunityDevWriteTools()
            with self.assertRaises(PermissionError):
                tools.create_forum_post("nope", "body")

    def test_platform_health_tool_call(self) -> None:
        self._initialize()
        response = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "platform_health", "arguments": {}},
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        content = (payload.get("result") or {}).get("content") or []
        text_block = next(item for item in content if item.get("type") == "text")
        parsed = json.loads(text_block["text"])
        self.assertEqual(parsed.get("status"), "ok")

    def test_query_data_collections_lists_forum_categories(self) -> None:
        from community.models import ForumCategory

        ForumCategory.objects.create(name="General", slug="general", description="Talk")
        self._initialize()
        response = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "query_data_collections",
                    "arguments": {
                        "collection": "forumcategory",
                        "search_pipeline": [{"$limit": 10}],
                    },
                },
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        content = (payload.get("result") or {}).get("content") or []
        text_block = next(item for item in content if item.get("type") == "text")
        rows = json.loads(text_block["text"])
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["slug"], "general")

    def test_query_tool_documents_model_collections(self) -> None:
        self._initialize()
        response = self._post_mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = (response.json().get("result") or {}).get("tools") or []
        query_tool = next(item for item in tools if item["name"] == "query_data_collections")
        description = query_tool.get("description", "").lower()
        for snippet in ("forumcategory", "product", "listing", "user", "game"):
            self.assertIn(snippet, description)
