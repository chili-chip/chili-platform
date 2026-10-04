from __future__ import annotations

from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from app.d1_sql import (
    clean_sqlmigrate_output,
    index_existing_d1_files,
    migration_file_name,
    migrations_in_plan_order,
)


class Command(BaseCommand):
    help = (
        "Write Wrangler D1 SQL migration files from Django sqlmigrate output "
        "(repo migrations/ directory by default)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "app_label",
            nargs="?",
            help="Optional app label; limits generation to that app.",
        )
        parser.add_argument(
            "migration_name",
            nargs="?",
            help="Optional migration name (with app_label).",
        )
        parser.add_argument(
            "--output-dir",
            type=Path,
            default=None,
            help="Directory for .sql files (default: <repo>/migrations).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing SQL files for the selected migrations.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print paths that would be written without creating files.",
        )

    def handle(self, *args, **options):
        output_dir = options["output_dir"]
        if output_dir is None:
            output_dir = settings.BASE_DIR.parent / "migrations"
        output_dir = output_dir.resolve()

        app_filter = options["app_label"]
        migration_filter = options["migration_name"]
        if migration_filter and not app_filter:
            raise CommandError("migration_name requires app_label.")
        if app_filter and not migration_filter:
            selected = [
                step for step in migrations_in_plan_order() if step[0] == app_filter
            ]
        elif app_filter and migration_filter:
            selected = [(app_filter, migration_filter)]
        else:
            selected = migrations_in_plan_order()

        if not selected:
            raise CommandError("No migrations matched the given filters.")

        plan = migrations_in_plan_order()
        order_index = {step: index for index, step in enumerate(plan, start=1)}
        for step in selected:
            if step not in order_index:
                raise CommandError(f"Unknown migration {step[0]}.{step[1]}")

        existing = index_existing_d1_files(output_dir)
        written = 0
        skipped_no_sql = 0
        skipped_exists = 0

        for app_label, migration_name in selected:
            index = order_index[(app_label, migration_name)]
            filename = migration_file_name(index, app_label, migration_name)
            path = output_dir / filename

            if (app_label, migration_name) in existing and not options["force"]:
                if existing[(app_label, migration_name)] != path:
                    raise CommandError(
                        f"Migration {app_label}.{migration_name} already has "
                        f"{existing[(app_label, migration_name)].name}; use --force."
                    )
                skipped_exists += 1
                self.stdout.write(f"Exists: {filename}")
                continue

            buffer = StringIO()
            call_command(
                "sqlmigrate",
                app_label,
                migration_name,
                stdout=buffer,
                verbosity=0,
            )
            sql = clean_sqlmigrate_output(buffer.getvalue())
            if sql is None:
                skipped_no_sql += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Skip (no SQL): {app_label}.{migration_name}"
                    )
                )
                continue

            if options["dry_run"]:
                self.stdout.write(f"Would write: {path}")
                written += 1
                continue

            output_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(sql, encoding="utf-8")
            written += 1
            self.stdout.write(self.style.SUCCESS(f"Wrote {filename}"))

        summary = f"Done ({written} written"
        if skipped_exists:
            summary += f", {skipped_exists} already present"
        if skipped_no_sql:
            summary += f", {skipped_no_sql} without SQL"
        summary += ")."
        self.stdout.write(self.style.SUCCESS(summary))
