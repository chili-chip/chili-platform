"""MCP read tools for community forum models."""

from __future__ import annotations

from mcp_server import ModelQueryToolset

from app.mcp_helpers import ChiliPlatformTools
from community.models import ForumCategory, ForumComment, ForumPost


class ForumCategoryQueryTool(ModelQueryToolset):
    model = ForumCategory
    search_fields = ["name", "slug", "description"]
    extra_instructions = "Forum categories; staff manage writes via the REST API or admin."


class ForumPostQueryTool(ModelQueryToolset):
    model = ForumPost
    exclude_fields = []
    search_fields = ["title", "slug", "content"]
    extra_instructions = "Public forum posts (read-only over MCP)."


class ForumCommentQueryTool(ModelQueryToolset):
    model = ForumComment
    search_fields = ["content"]
    extra_instructions = "Public forum comments (read-only over MCP)."


# Re-export for autodiscovery side effect on MCPToolset registry.
__all__ = ["ChiliPlatformTools", "ForumCategoryQueryTool", "ForumPostQueryTool", "ForumCommentQueryTool"]
