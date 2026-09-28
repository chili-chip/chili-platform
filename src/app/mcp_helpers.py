"""Shared helpers for django-mcp-server ModelQueryToolsets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_server import MCPToolset

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet
    from django.http import HttpRequest


class ChiliPlatformTools(MCPToolset):
    def platform_health(self) -> dict[str, str]:
        """Return the same payload as GET /api/health/ (service liveness)."""
        return {"status": "ok", "service": "chili-platform"}


def mcp_user(request: HttpRequest | None) -> AbstractBaseUser | None:
    if request is None:
        return None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    return None


class AuthenticatedOwnerMixin:
    """Mixin: scope queryset to an authenticated user field."""

    owner_field = "user"

    def get_queryset(self) -> QuerySet:
        user = mcp_user(self.request)
        if user is None:
            return self.model._default_manager.none()
        return super().get_queryset().filter(**{self.owner_field: user})
