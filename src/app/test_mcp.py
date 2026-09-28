from __future__ import annotations

import json

from django.test import Client, TestCase, override_settings

_MCP_PROTOCOL_VERSION = "2026-07-28"
_MCP_REQUEST_META = {
    "io.modelcontextprotocol/protocolVersion": _MCP_PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
}


@override_settings(MCP_ENABLED=True)
class McpEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def _post_mcp(self, method: str, params: dict | None = None, *, tool_name: str | None = None):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {**(params or {}), "_meta": _MCP_REQUEST_META},
        }
        headers = {
            "content_type": "application/json",
            "HTTP_ACCEPT": "application/json",
            "HTTP_MCP_PROTOCOL_VERSION": _MCP_PROTOCOL_VERSION,
            "HTTP_MCP_METHOD": method,
        }
        if tool_name is not None:
            headers["HTTP_MCP_NAME"] = tool_name
        return self.client.post("/mcp/", data=json.dumps(body), **headers)

    def test_tools_list_includes_platform_tools(self) -> None:
        response = self._post_mcp("tools/list")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload.get("jsonrpc"), "2.0")
        result = payload.get("result") or {}
        tools = result.get("tools") or []
        names = {tool["name"] for tool in tools}
        self.assertIn("platform_health", names)
        self.assertIn("list_forum_categories", names)

    def test_platform_health_tool_call(self) -> None:
        response = self._post_mcp(
            "tools/call",
            {"name": "platform_health", "arguments": {}},
            tool_name="platform_health",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        structured = (payload.get("result") or {}).get("structuredContent") or {}
        self.assertEqual(structured.get("status"), "ok")
        self.assertEqual(structured.get("service"), "chili-platform")
