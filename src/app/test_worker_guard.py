"""Worker vs local Django settings detection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

_SRC = Path(__file__).resolve().parent.parent


def _probe_settings(extra_env: dict[str, str]) -> tuple[bool, str]:
    """Import settings in a fresh interpreter and return ON_WORKERS + DB engine."""
    env = os.environ.copy()
    env.update(extra_env)
    env.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
    for key in ("CHILI_LOCAL_DJANGO", "WORKERS_CI"):
        if key not in extra_env and key in env:
            del env[key]
    code = f"""
import sys
sys.path.insert(0, {str(_SRC)!r})
import app.settings as s
print(int(s.ON_WORKERS))
engine = s.DATABASES.get("default", {{}}).get("ENGINE", "")
print(engine)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    lines = result.stdout.strip().splitlines()
    on_workers = bool(int(lines[0]))
    engine = lines[1] if len(lines) > 1 else ""
    return on_workers, engine


class WorkerGuardTests(SimpleTestCase):
    def test_workers_ci_uses_empty_databases(self) -> None:
        on_workers, engine = _probe_settings({"WORKERS_CI": "1"})
        self.assertFalse(on_workers)
        self.assertEqual(engine, "")

    def test_local_manage_flag_uses_sqlite(self) -> None:
        on_workers, engine = _probe_settings({"CHILI_LOCAL_DJANGO": "1"})
        self.assertFalse(on_workers)
        self.assertIn("sqlite3", engine)

    def test_manage_py_sets_local_flag(self) -> None:
        manage = _SRC / "manage.py"
        text = manage.read_text(encoding="utf-8")
        self.assertIn('os.environ["CHILI_LOCAL_DJANGO"] = "1"', text)
