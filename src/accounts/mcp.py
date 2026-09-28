"""MCP read tools for accounts models."""

from __future__ import annotations

from mcp_server import ModelQueryToolset

from accounts.models import User


class UserProfileQueryTool(ModelQueryToolset):
    model = User
    exclude_fields = [
        "password",
        "stripe_customer_id",
        "is_staff",
        "is_superuser",
        "is_active",
        "last_login",
        "groups",
        "user_permissions",
    ]
    fields = [
        "id",
        "username",
        "first_name",
        "last_name",
        "avatar_url",
        "bio",
        "date_joined",
        "created_at",
    ]
    search_fields = ["username", "bio"]
    extra_instructions = (
        "Public profile fields only (same spirit as GET /api/profiles/<username>/). "
        "No passwords or Stripe customer IDs."
    )
