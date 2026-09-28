"""MCP read tools for Bitsy games."""

from __future__ import annotations

from mcp_server import ModelQueryToolset

from games.models import Game


class ReleasedGameQueryTool(ModelQueryToolset):
    model = Game
    exclude_fields = ["data"]
    search_fields = ["title", "slug"]
    extra_instructions = (
        "Released games only; Bitsy `data` is excluded (same restriction as anonymous API). "
        "Owners' private projects are not listed without MCP authentication."
    )

    def get_queryset(self):
        return super().get_queryset().filter(released=True)
