"""Cloudflare Worker entrypoint for Chili Platform.

D1's django-cf backend is synchronous. Worker `fetch` is async, so Django
would raise SynchronousOnlyOperation on ORM calls (admin login, JWT, etc.)
unless we go through django-cf's WSGI helper, which sets
DJANGO_ALLOW_ASYNC_UNSAFE.
"""

from __future__ import annotations

import os

from app.hashlib_compat import install as install_pbkdf2

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
install_pbkdf2()

from django.core.wsgi import get_wsgi_application
from django_cf import DjangoCF
from workers import WorkerEntrypoint

application = get_wsgi_application()


class Default(DjangoCF, WorkerEntrypoint):
    def get_app(self):
        return application
