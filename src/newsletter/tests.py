from __future__ import annotations

import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.mail import MailDeliveryError
from accounts.models import UserSettings
from newsletter.models import Delivery, Issue, Subscriber
from newsletter.services import recipient_emails, send_issue_batch
from newsletter.tokens import make_unsubscribe_token

User = get_user_model()

LOC_MEM = "django.core.mail.backends.locmem.EmailBackend"


def _token_from_body(body: str, path: str) -> str:
    match = re.search(r"https?://\S+" + re.escape(path) + r"\?\S+", body)
    assert match, body
    return parse_qs(urlparse(match.group(0)).query)["token"][0]


@override_settings(EMAIL_BACKEND=LOC_MEM, FRONTEND_BASE_URL="http://localhost:4200", ON_WORKERS=False)
class SubscriptionTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def subscribe(self, email):
        return self.client.post("/api/newsletter/subscribe/", {"email": email}, format="json")

    def test_double_opt_in(self):
        response = self.subscribe("Fan@Example.com")
        self.assertEqual(response.status_code, 202)
        subscriber = Subscriber.objects.get()
        self.assertEqual(subscriber.email, "fan@example.com")
        self.assertFalse(subscriber.is_active)
        self.assertEqual(recipient_emails(), [])

        token = _token_from_body(mail.outbox[-1].body, "/newsletter/confirm")
        response = self.client.post("/api/newsletter/confirm/", {"token": token}, format="json")
        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertTrue(subscriber.is_active)
        self.assertEqual(recipient_emails(), ["fan@example.com"])

    def test_active_subscriber_gets_same_reply_and_no_mail(self):
        Subscriber.objects.create(email="fan@example.com", confirmed_at="2026-01-01T00:00:00Z")
        response = self.subscribe("fan@example.com")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(mail.outbox), 0)

    def test_bad_tokens_rejected(self):
        for path in ("/api/newsletter/confirm/", "/api/newsletter/unsubscribe/"):
            response = self.client.post(path, {"token": "nope"}, format="json")
            self.assertEqual(response.status_code, 400)
        # An unsubscribe token is not a confirm token.
        token = make_unsubscribe_token("fan@example.com")
        response = self.client.post("/api/newsletter/confirm/", {"token": token}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_unsubscribe_stops_form_and_account_subscriptions(self):
        Subscriber.objects.create(email="fan@example.com", confirmed_at="2026-01-01T00:00:00Z")
        user = User.objects.create_user("fan", "Fan@example.com", "pw", email_verified=True)
        UserSettings.objects.create(user=user, newsletter_opt_in=True)
        self.assertEqual(recipient_emails(), ["fan@example.com"])

        token = make_unsubscribe_token("fan@example.com")
        response = self.client.post("/api/newsletter/unsubscribe/", {"token": token}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recipient_emails(), [])
        self.assertFalse(UserSettings.objects.get(user=user).newsletter_opt_in)

    def test_settings_opt_out_also_unsubscribes_form_address(self):
        user = User.objects.create_user("fan", "fan@example.com", "pw", email_verified=True)
        UserSettings.objects.create(user=user, newsletter_opt_in=True)
        Subscriber.objects.create(email="fan@example.com", confirmed_at="2026-01-01T00:00:00Z")
        self.client.force_authenticate(user)
        response = self.client.patch(
            "/api/profiles/me/settings/", {"newsletter_opt_in": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recipient_emails(), [])

    def test_unverified_account_opt_in_is_not_a_recipient(self):
        user = User.objects.create_user("new", "new@example.com", "pw", email_verified=False)
        UserSettings.objects.create(user=user, newsletter_opt_in=True)
        self.assertEqual(recipient_emails(), [])


@override_settings(
    EMAIL_BACKEND=LOC_MEM,
    FRONTEND_BASE_URL="http://localhost:4200",
    ON_WORKERS=False,
    NEWSLETTER_SEND_BATCH=2,
)
class IssueTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            "staff", "staff@example.com", "pw", is_staff=True, email_verified=True
        )
        for n in range(3):
            Subscriber.objects.create(email=f"s{n}@example.com", confirmed_at="2026-01-01T00:00:00Z")
        Subscriber.objects.create(email="pending@example.com")

    def test_only_staff_manage_issues(self):
        user = User.objects.create_user("fan", "fan@example.com", "pw")
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get("/api/newsletter/issues/").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/newsletter/issues/").status_code, 401)

    def test_send_in_batches_once_per_address(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            "/api/newsletter/issues/", {"subject": "News", "body": "Hello"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        issue_id = response.data["id"]
        self.assertEqual(self.client.get("/api/newsletter/issues/audience/").data, {"recipients": 3})

        first = self.client.post(f"/api/newsletter/issues/{issue_id}/send/")
        self.assertEqual((first.data["sent"], first.data["remaining"]), (2, 1))
        self.assertIsNone(first.data["issue"]["sent_at"])

        # Sending started, so the issue is frozen.
        edit = self.client.patch(f"/api/newsletter/issues/{issue_id}/", {"body": "x"}, format="json")
        self.assertEqual(edit.status_code, 400)

        second = self.client.post(f"/api/newsletter/issues/{issue_id}/send/")
        self.assertEqual((second.data["sent"], second.data["remaining"]), (1, 0))
        self.assertIsNotNone(second.data["issue"]["sent_at"])
        self.assertEqual(second.data["issue"]["sent_count"], 3)

        again = self.client.post(f"/api/newsletter/issues/{issue_id}/send/")
        self.assertEqual(again.data["sent"], 0)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), [f"s{n}@example.com" for n in range(3)])
        for message in mail.outbox:
            self.assertIn("Hello", message.body)
            token = _token_from_body(message.body, "/newsletter/unsubscribe")
            self.assertTrue(token)

    def test_failed_address_is_recorded_and_not_retried(self):
        issue = Issue.objects.create(subject="News", body="Hello")
        calls = []

        def flaky(*, to, subject, body):
            calls.append(to)
            if to == "s0@example.com":
                raise MailDeliveryError("rejected")

        with patch("newsletter.services.send_plain_email", side_effect=flaky):
            result = send_issue_batch(issue, limit=10)
        self.assertEqual(result, {"sent": 2, "failed": 1, "remaining": 0})
        self.assertEqual(Delivery.objects.get(email="s0@example.com").error, "rejected")

    def test_send_test_goes_to_staff_only(self):
        issue = Issue.objects.create(subject="News", body="Hello")
        self.client.force_authenticate(self.staff)
        response = self.client.post(f"/api/newsletter/issues/{issue.id}/send-test/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [["staff@example.com"]])
        self.assertTrue(mail.outbox[0].subject.startswith("[Test]"))
        self.assertFalse(issue.deliveries.exists())
