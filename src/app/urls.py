"""Root URL configuration."""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from app.media import serve_media


def health(_request):
    return JsonResponse({"status": "ok", "service": "chili-platform"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("games.urls")),
    path("api/forum/", include("community.urls")),
    path("api/store/", include("store.urls")),
    path("api/marketplace/", include("marketplace.urls")),
    path("api/_ops/", include("app.ops_urls")),
    path("media/<path:name>", serve_media, name="media"),
]

if settings.MCP_ENABLED:
    from django_stateless_mcp import mcp_view

    from app.mcp import server as mcp_server

    urlpatterns.append(path("mcp/", mcp_view(mcp_server), name="mcp"))
