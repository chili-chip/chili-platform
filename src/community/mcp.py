"""MCP tools for Chili Platform (django-mcp-server autodiscovery)."""

from __future__ import annotations

from mcp_server import MCPToolset

from community.models import ForumCategory


class ChiliPlatformTools(MCPToolset):
    def platform_health(self) -> dict[str, str]:
        """Return the same payload as GET /api/health/ (service liveness)."""
        return {"status": "ok", "service": "chili-platform"}

    def list_forum_categories(self) -> list[dict[str, str]]:
        """List community forum categories (public read, same data as the forum API)."""
        return [
            {
                "slug": category.slug,
                "name": category.name,
                "description": category.description,
            }
            for category in ForumCategory.objects.all()
        ]
