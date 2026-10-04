from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path
from unittest import TestCase

from django.core.management import call_command

from app.d1_sql import clean_sqlmigrate_output, migration_file_name


class D1SqlHelpersTests(TestCase):
    def test_clean_sqlmigrate_output_strips_transaction_and_comments(self):
        raw = """BEGIN;
--
-- Create model User
--
CREATE TABLE "accounts_user" ("id" integer NOT NULL PRIMARY KEY);
--
-- Raw Python operation
--
-- THIS OPERATION CANNOT BE WRITTEN AS SQL
COMMIT;
"""
        sql = clean_sqlmigrate_output(raw)
        self.assertEqual(
            sql,
            'CREATE TABLE "accounts_user" ("id" integer NOT NULL PRIMARY KEY);\n',
        )

    def test_migration_file_name(self):
        self.assertEqual(
            migration_file_name(17, "accounts", "0003_user_email_verified"),
            "0017_accounts_0003-user-email-verified.sql",
        )


class GenerateD1MigrationsCommandTests(TestCase):
    def test_writes_accounts_initial_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            call_command(
                "generate_d1_migrations",
                "accounts",
                "0001_initial",
                output_dir=out,
                stdout=StringIO(),
            )
            path = out / "0015_accounts_0001-initial.sql"
            self.assertTrue(path.is_file())
            body = path.read_text(encoding="utf-8")
            self.assertIn('CREATE TABLE "accounts_user"', body)
            self.assertNotIn("BEGIN;", body)
