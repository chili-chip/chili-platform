"""Chili Platform MCP server (django-stateless-mcp + MCP SDK)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from django_stateless_mcp import request_state_security

server = MCPServer(
    name="chili-platform",
    version="0.1.0",
    request_state_security=request_state_security(),
)


@server.tool()
def platform_health() -> dict[str, str]:
    """Return the same payload as GET /api/health/ (service liveness)."""
    return {"status": "ok", "service": "chili-platform"}


@server.tool()
def list_forum_categories() -> list[dict[str, str]]:
    """List community forum categories (public read, same data as the forum API)."""
    from community.models import ForumCategory

    return [
        {
            "slug": category.slug,
            "name": category.name,
            "description": category.description,
        }
        for category in ForumCategory.objects.all()
    ]
