"""MCP read tools for community forum models."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from mcp_server import MCPToolset, ModelQueryToolset

from app.mcp_helpers import ChiliPlatformTools
from community.models import ForumCategory, ForumComment, ForumPost


class CommunityDevWriteTools(MCPToolset):
    """Local MCP write helpers (dev only)."""

    def create_forum_post(
        self,
        title: str,
        content: str,
        category_slug: str = "general",
    ) -> dict[str, int | str]:
        """Create a forum post for local MCP testing (title, body, category slug)."""
        if not settings.MCP_ENABLED:
            raise PermissionError(
                "Forum MCP writes are disabled. Set MCP_ENABLED=true for local development."
            )

        author = get_user_model().objects.filter(
            username=settings.MCP_FORUM_POST_AUTHOR_USERNAME,
        ).first()
        if author is None:
            raise ValueError(
                f"MCP forum author {settings.MCP_FORUM_POST_AUTHOR_USERNAME!r} not found. "
                "Run ops bootstrap or create that user locally."
            )

        category = ForumCategory.objects.filter(slug=category_slug).first()
        if category is None:
            raise ValueError(
                f"Unknown category slug {category_slug!r}. "
                "Run `uv run python src/manage.py seed_forum` first."
            )

        post = ForumPost.objects.create(
            title=title,
            content=content,
            author=author,
            category=category,
        )
        return {
            "id": post.id,
            "slug": post.slug,
            "title": post.title,
            "category_slug": category.slug,
            "community_path": f"/community/post/{post.id}",
        }


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


__all__ = [
    "ChiliPlatformTools",
    "CommunityDevWriteTools",
    "ForumCategoryQueryTool",
    "ForumPostQueryTool",
    "ForumCommentQueryTool",
]
