from __future__ import annotations

import json

from django.test import Client, TestCase, override_settings


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

    def test_initialize_and_list_tools(self) -> None:
        init = self._post_mcp(
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
        self.assertEqual(init.status_code, 200, init.content)

        listed = self._post_mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(listed.status_code, 200, listed.content)
        payload = listed.json()
        tools = (payload.get("result") or {}).get("tools") or []
        names = {tool["name"] for tool in tools}
        self.assertIn("platform_health", names)
        self.assertIn("list_forum_categories", names)

    def test_platform_health_tool_call(self) -> None:
        self._post_mcp(
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
        self.assertTrue(content)
        text_block = next(item for item in content if item.get("type") == "text")
        parsed = json.loads(text_block["text"])
        self.assertEqual(parsed.get("status"), "ok")
        self.assertEqual(parsed.get("service"), "chili-platform")
