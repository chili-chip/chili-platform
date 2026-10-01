"""Fail-closed Worker boot checks. Local SQLite and wrangler dev stay allowed."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from app.worker_guard import (
    PLACEHOLDER_D1_DATABASE_ID,
    SETTINGS_DEV_OPS_TOKEN,
    SETTINGS_DEV_SECRET_KEY,
    _load_embed,
    dev_defaults_from_example,
    enforce_worker_boot,
    is_local_or_wildcard,
    parse_d1_database_id,
    render_worker_boot_embed,
    worker_boot_problems,
    write_worker_boot_embed,
)

ROOT = Path(__file__).resolve().parents[2]

CHECKOUT_URLS = {
    "STORE_CHECKOUT_SUCCESS_URL": (
        "http://localhost:4200/store/checkout/success?session_id={CHECKOUT_SESSION_ID}"
    ),
    "STORE_CHECKOUT_CANCEL_URL": "http://localhost:4200/store/checkout/cancel",
    "MARKETPLACE_CHECKOUT_SUCCESS_URL": (
        "http://localhost:4200/marketplace/checkout/success?session_id={CHECKOUT_SESSION_ID}"
    ),
    "MARKETPLACE_CHECKOUT_CANCEL_URL": "http://localhost:4200/marketplace/checkout/cancel",
}


def _problems(**overrides) -> list[str]:
    payload = {
        "on_workers": True,
        "wrangler_command": "deploy",
        "secret_key": "production-secret",
        "ops_token": "production-ops-token",
        "dev_secret_keys": frozenset({SETTINGS_DEV_SECRET_KEY, "example-secret"}),
        "dev_ops_tokens": frozenset({SETTINGS_DEV_OPS_TOKEN, "example-ops"}),
        "allowed_hosts": ["chili-platform-api.example.workers.dev"],
        "cors_origins": ["https://chili-platform.pages.dev"],
        "checkout_urls": {
            "STORE_CHECKOUT_SUCCESS_URL": (
                "https://chili-platform.pages.dev/store/checkout/success"
                "?session_id={CHECKOUT_SESSION_ID}"
            ),
            "STORE_CHECKOUT_CANCEL_URL": "https://chili-platform.pages.dev/store/checkout/cancel",
            "MARKETPLACE_CHECKOUT_SUCCESS_URL": (
                "https://chili-platform.pages.dev/marketplace/checkout/success"
                "?session_id={CHECKOUT_SESSION_ID}"
            ),
            "MARKETPLACE_CHECKOUT_CANCEL_URL": (
                "https://chili-platform.pages.dev/marketplace/checkout/cancel"
            ),
        },
        "public_base_url": "https://chili-platform-api.example.workers.dev",
        "d1_database_id": "11111111-1111-1111-1111-111111111111",
    }
    payload.update(overrides)
    return worker_boot_problems(**payload)


class WorkerBootGuardTests(SimpleTestCase):
    def test_local_sqlite_and_wrangler_dev_keep_local_defaults(self) -> None:
        local = {
            "secret_key": SETTINGS_DEV_SECRET_KEY,
            "ops_token": SETTINGS_DEV_OPS_TOKEN,
            "allowed_hosts": ["*"],
            "cors_origins": ["http://localhost:4200", "http://127.0.0.1:4200"],
            "checkout_urls": CHECKOUT_URLS,
            "public_base_url": "http://localhost:8787",
            "d1_database_id": PLACEHOLDER_D1_DATABASE_ID,
        }
        self.assertEqual(_problems(on_workers=False, **local), [])
        self.assertEqual(_problems(wrangler_command="dev", **local), [])

    def test_deploy_rejects_settings_and_example_dev_secrets(self) -> None:
        secrets, tokens = dev_defaults_from_example(
            "DJANGO_SECRET_KEY=example-secret\nOPS_TOKEN=example-ops\n"
        )
        self.assertIn(SETTINGS_DEV_SECRET_KEY, secrets)
        self.assertIn("example-secret", secrets)
        self.assertIn(SETTINGS_DEV_OPS_TOKEN, tokens)
        self.assertIn("example-ops", tokens)
        for secret, token in (
            (SETTINGS_DEV_SECRET_KEY, "production-ops-token"),
            ("example-secret", "production-ops-token"),
            ("production-secret", SETTINGS_DEV_OPS_TOKEN),
            ("production-secret", "example-ops"),
        ):
            problems = _problems(
                secret_key=secret,
                ops_token=token,
                dev_secret_keys=secrets,
                dev_ops_tokens=tokens,
            )
            self.assertTrue(problems, (secret, token))

    def test_deploy_rejects_localhost_loopback_and_wildcard(self) -> None:
        self.assertTrue(is_local_or_wildcard("*"))
        self.assertTrue(is_local_or_wildcard("localhost"))
        self.assertTrue(is_local_or_wildcard("http://LOCALHOST:4200/store"))
        self.assertTrue(is_local_or_wildcard("http://127.0.0.1:4200"))
        self.assertTrue(is_local_or_wildcard("http://[::1]:8787/media"))
        self.assertFalse(is_local_or_wildcard("https://chili-platform.pages.dev"))

        problems = _problems(
            allowed_hosts=["*"],
            cors_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
            checkout_urls=CHECKOUT_URLS,
            public_base_url="http://localhost:8787",
        )
        text = "\n".join(problems)
        self.assertIn("DJANGO_ALLOWED_HOSTS", text)
        self.assertIn("CORS_ALLOWED_ORIGINS", text)
        self.assertIn("http://localhost:4200", text)
        self.assertIn("http://127.0.0.1:4200", text)
        for name in CHECKOUT_URLS:
            self.assertIn(name, text)
        self.assertIn("PUBLIC_BASE_URL", text)
        self.assertNotIn("production-secret", text)

    def test_deploy_rejects_placeholder_database_id(self) -> None:
        missing = _problems(d1_database_id=None)
        placeholder = _problems(d1_database_id=PLACEHOLDER_D1_DATABASE_ID)
        self.assertIn("missing", "\n".join(missing))
        self.assertIn(PLACEHOLDER_D1_DATABASE_ID, "\n".join(placeholder))

    def test_versions_upload_is_treated_as_deploy(self) -> None:
        problems = _problems(
            wrangler_command="versions upload",
            secret_key=SETTINGS_DEV_SECRET_KEY,
        )
        self.assertTrue(any("DJANGO_SECRET_KEY" in item for item in problems))

    def test_production_values_boot(self) -> None:
        self.assertEqual(_problems(), [])

    def test_committed_wrangler_config_stays_local_and_would_refuse_deploy(self) -> None:
        wrangler = (ROOT / "wrangler.jsonc").read_text()
        example = (ROOT / ".dev.vars.example").read_text()
        self.assertIn('"DJANGO_DEBUG": "false"', wrangler)
        self.assertNotIn("MCP_ENABLED", wrangler)
        self.assertNotIn('"MARKETPLACE_PROCESSING_FEE_BPS"', wrangler)
        self.assertNotIn('"MARKETPLACE_PROCESSING_FEE_FIXED_CENTS"', wrangler)
        self.assertEqual(parse_d1_database_id(wrangler), PLACEHOLDER_D1_DATABASE_ID)
        secrets, tokens = dev_defaults_from_example(example)
        self.assertIn(SETTINGS_DEV_SECRET_KEY, secrets)
        self.assertIn(SETTINGS_DEV_OPS_TOKEN, tokens)
        problems = _problems(
            secret_key=SETTINGS_DEV_SECRET_KEY,
            ops_token=SETTINGS_DEV_OPS_TOKEN,
            dev_secret_keys=secrets,
            dev_ops_tokens=tokens,
            allowed_hosts=["*"],
            cors_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
            checkout_urls=CHECKOUT_URLS,
            public_base_url="http://localhost:8787",
            d1_database_id=parse_d1_database_id(wrangler),
        )
        text = "\n".join(problems)
        for label in (
            "DJANGO_SECRET_KEY",
            "OPS_TOKEN",
            "DJANGO_ALLOWED_HOSTS",
            "CORS_ALLOWED_ORIGINS",
            "STORE_CHECKOUT_SUCCESS_URL",
            "STORE_CHECKOUT_CANCEL_URL",
            "MARKETPLACE_CHECKOUT_SUCCESS_URL",
            "MARKETPLACE_CHECKOUT_CANCEL_URL",
            "PUBLIC_BASE_URL",
            PLACEHOLDER_D1_DATABASE_ID,
        ):
            self.assertIn(label, text)

    def test_processing_fees_stay_unset(self) -> None:
        self.assertIsNone(settings.MARKETPLACE_PROCESSING_FEE_BPS)
        self.assertIsNone(settings.MARKETPLACE_PROCESSING_FEE_FIXED_CENTS)
        if not os.environ.get("DJANGO_SECRET_KEY"):
            self.assertEqual(settings.SECRET_KEY, SETTINGS_DEV_SECRET_KEY)
        if not os.environ.get("OPS_TOKEN"):
            self.assertEqual(settings.OPS_TOKEN, SETTINGS_DEV_OPS_TOKEN)

    def test_embed_round_trip_and_missing_module(self) -> None:
        source = render_worker_boot_embed(
            d1_database_id=PLACEHOLDER_D1_DATABASE_ID,
            wrangler_command="deploy",
            dev_secret_keys=frozenset({SETTINGS_DEV_SECRET_KEY}),
            dev_ops_tokens=frozenset({SETTINGS_DEV_OPS_TOKEN}),
        )
        namespace: dict = {}
        exec(source, namespace)  # noqa: S102
        self.assertEqual(namespace["D1_DATABASE_ID"], PLACEHOLDER_D1_DATABASE_ID)
        self.assertEqual(namespace["WRANGLER_COMMAND"], "deploy")
        self.assertEqual(namespace["DEV_SECRET_KEYS"], (SETTINGS_DEV_SECRET_KEY,))

        with patch("importlib.import_module", side_effect=ImportError("missing")):
            with self.assertRaises(ImproperlyConfigured) as raised:
                _load_embed()
        self.assertIn("wrangler.jsonc", str(raised.exception))

    def test_enforce_raises_on_deploy_and_allows_dev(self) -> None:
        class Embed:
            WRANGLER_COMMAND = "deploy"
            D1_DATABASE_ID = PLACEHOLDER_D1_DATABASE_ID
            DEV_SECRET_KEYS = (SETTINGS_DEV_SECRET_KEY,)
            DEV_OPS_TOKENS = (SETTINGS_DEV_OPS_TOKEN,)

        with self.assertRaises(ImproperlyConfigured) as raised:
            enforce_worker_boot(
                secret_key=SETTINGS_DEV_SECRET_KEY,
                ops_token=SETTINGS_DEV_OPS_TOKEN,
                allowed_hosts=["*"],
                cors_origins=["http://localhost:4200"],
                checkout_urls=CHECKOUT_URLS,
                public_base_url="http://localhost:8787",
                embed=Embed(),
            )
        self.assertIn("Refusing to boot", str(raised.exception))
        self.assertNotIn(SETTINGS_DEV_SECRET_KEY, str(raised.exception))

        class DevEmbed:
            WRANGLER_COMMAND = "dev"
            D1_DATABASE_ID = PLACEHOLDER_D1_DATABASE_ID
            DEV_SECRET_KEYS = (SETTINGS_DEV_SECRET_KEY,)
            DEV_OPS_TOKENS = (SETTINGS_DEV_OPS_TOKEN,)

        enforce_worker_boot(
            secret_key=SETTINGS_DEV_SECRET_KEY,
            ops_token=SETTINGS_DEV_OPS_TOKEN,
            allowed_hosts=["*"],
            cors_origins=["http://localhost:4200"],
            checkout_urls=CHECKOUT_URLS,
            public_base_url="http://localhost:8787",
            embed=DevEmbed(),
        )

    def test_write_embed_reads_wrangler_without_inventing_an_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "app").mkdir(parents=True)
            (root / "wrangler.jsonc").write_text(
                '{ "d1_databases": [ { "database_id": "%s" } ] }\n' % PLACEHOLDER_D1_DATABASE_ID
            )
            (root / ".dev.vars.example").write_text(
                "DJANGO_SECRET_KEY=example-secret\nOPS_TOKEN=example-ops\n"
            )
            destination = write_worker_boot_embed(root, wrangler_command="deploy")
            text = destination.read_text()
            self.assertIn(repr(PLACEHOLDER_D1_DATABASE_ID), text)
            self.assertIn("WRANGLER_COMMAND = 'deploy'", text)
            self.assertIn("example-secret", text)
            self.assertIn("example-ops", text)
            self.assertNotIn("11111111-1111-1111-1111-111111111111", text)
