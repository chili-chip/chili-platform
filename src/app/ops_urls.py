"""Protected ops endpoints for D1 migrate/seed from wrangler."""

from __future__ import annotations

from django.urls import path

from app.ops_views import bootstrap_view, migrate_view, seed_view

urlpatterns = [
    path("migrate/", migrate_view, name="ops-migrate"),
    path("seed/", seed_view, name="ops-seed"),
    path("bootstrap/", bootstrap_view, name="ops-bootstrap"),
]
