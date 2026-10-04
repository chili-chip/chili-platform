"""Worker index entrypoint: lazy WSGI init (avoids deploy error 10021)."""

from __future__ import annotations

import sys
import types
from unittest import mock

from django.test import SimpleTestCase


def _load_index_module():
    """Import src/index.py with workers/django_cf stubs (no Pyodide `js` module)."""
    workers = types.ModuleType("workers")
    workers.WorkerEntrypoint = object
    workers.wsgi = types.ModuleType("workers.wsgi")

    django_cf = types.ModuleType("django_cf")

    class DjangoCF:
        async def fetch(self, request):
            raise NotImplementedError

    django_cf.DjangoCF = DjangoCF
    django_cf.handle_wsgi = mock.AsyncMock()

    with mock.patch.dict(
        sys.modules,
        {
            "workers": workers,
            "workers.wsgi": workers.wsgi,
            "django_cf": django_cf,
        },
    ):
        import importlib.util
        from pathlib import Path

        index_path = Path(__file__).resolve().parents[1] / "index.py"
        spec = importlib.util.spec_from_file_location("chili_worker_index", index_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["chili_worker_index"] = module
        spec.loader.exec_module(module)
        return module


class WorkerIndexLazyWsgiTests(SimpleTestCase):
    def test_get_wsgi_application_not_called_at_import(self) -> None:
        with mock.patch("django.core.wsgi.get_wsgi_application") as get_wsgi:
            module = _load_index_module()
            get_wsgi.assert_not_called()
            self.assertIsNone(module.Default._wsgi_app)

    def test_get_app_loads_and_caches_wsgi_application(self) -> None:
        sentinel = object()
        with mock.patch(
            "django.core.wsgi.get_wsgi_application",
            return_value=sentinel,
        ) as get_wsgi:
            module = _load_index_module()
            worker = module.Default()

            self.assertIs(worker.get_app(), sentinel)
            self.assertIs(worker.get_app(), sentinel)
            get_wsgi.assert_called_once()
