"""Helpers for turning Django sqlmigrate output into Wrangler D1 SQL files."""

from __future__ import annotations

import re
from pathlib import Path

from django.db import connection
from django.db.migrations.loader import MigrationLoader

_NO_SQL_MARKER = "THIS OPERATION CANNOT BE WRITTEN AS SQL"
_FILENAME_RE = re.compile(
    r"^(?P<index>\d{4})_(?P<app>[a-z_]+)_(?P<slug>[\w-]+)\.sql$"
)


def migration_slug(migration_name: str) -> str:
    return migration_name.replace("_", "-")


def migration_file_name(index: int, app_label: str, migration_name: str) -> str:
    return f"{index:04d}_{app_label}_{migration_slug(migration_name)}.sql"


def migration_file_suffix(app_label: str, migration_name: str) -> str:
    return f"_{app_label}_{migration_slug(migration_name)}.sql"


def migrations_in_plan_order() -> list[tuple[str, str]]:
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    leaves = sorted(loader.graph.leaf_nodes())
    plan: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for leaf in leaves:
        for step in loader.graph.forwards_plan(leaf):
            if step not in seen:
                plan.append(step)
                seen.add(step)
    return plan


def clean_sqlmigrate_output(raw: str) -> str | None:
    """Strip transaction wrappers and non-SQL operations; None if empty."""
    statements: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped in {"BEGIN;", "COMMIT;"}:
            continue
        if _NO_SQL_MARKER in line:
            continue
        if stripped.startswith("--"):
            continue
        if stripped:
            statements.append(stripped)
    if not statements:
        return None
    return "\n".join(statements) + "\n"


def index_existing_d1_files(output_dir: Path) -> dict[tuple[str, str], Path]:
    """Map (app_label, migration_name) to an existing SQL file path."""
    indexed: dict[tuple[str, str], Path] = {}
    if not output_dir.is_dir():
        return indexed
    for path in sorted(output_dir.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            continue
        app_label = match.group("app")
        migration_name = match.group("slug").replace("-", "_")
        indexed[(app_label, migration_name)] = path
    return indexed
