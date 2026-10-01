"""Ops token checks use a constant-time compare."""

from __future__ import annotations

from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from app.ops_views import _authorized, migrate_view


class OpsTokenTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def _request(self, token: str | None):
        headers = {}
        if token is not None:
            headers["HTTP_X_OPS_TOKEN"] = token
        return self.factory.post("/api/_ops/migrate/", **headers)

    @override_settings(OPS_TOKEN="s3cret-token")
    def test_matching_token_uses_compare_digest(self) -> None:
        with patch("app.ops_views.hmac.compare_digest", return_value=True) as digest:
            self.assertTrue(_authorized(self._request("s3cret-token")))
        digest.assert_called_once_with(b"s3cret-token", b"s3cret-token")

    @override_settings(OPS_TOKEN="s3cret-token")
    def test_wrong_token_is_rejected(self) -> None:
        self.assertFalse(_authorized(self._request("s3cret-tokEn")))
        self.assertFalse(_authorized(self._request("nope")))
        self.assertFalse(_authorized(self._request("s3cret-token-extra")))

    @override_settings(OPS_TOKEN="")
    def test_empty_expected_token_never_authorizes(self) -> None:
        with patch("app.ops_views.hmac.compare_digest") as digest:
            self.assertFalse(_authorized(self._request("")))
            self.assertFalse(_authorized(self._request("s3cret-token")))
        digest.assert_not_called()

    @override_settings(OPS_TOKEN="s3cret-token")
    def test_missing_header_is_rejected(self) -> None:
        self.assertFalse(_authorized(self._request(None)))

    @override_settings(OPS_TOKEN="s3cret-token")
    def test_migrate_rejects_a_bad_token_without_running_commands(self) -> None:
        with patch("app.ops_views.call_command") as command:
            response = migrate_view(self._request("nope"))
        self.assertEqual(response.status_code, 401)
        command.assert_not_called()

    @override_settings(OPS_TOKEN="s3cret-token")
    def test_migrate_accepts_a_matching_token(self) -> None:
        with patch("app.ops_views.call_command") as command:
            response = migrate_view(self._request("s3cret-token"))
        self.assertEqual(response.status_code, 200)
        command.assert_called_once()
