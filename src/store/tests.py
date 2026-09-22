from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store.models import Order, Product
from store.stripe import StripeError, flatten_params, verify_webhook_payload

User = get_user_model()

WEBHOOK_SECRET = "whsec_test_secret"


def sign_webhook(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time() if timestamp is None else timestamp)
    signed = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


@override_settings(
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET,
)
class StoreApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password="supersecret",
        )
        self.staff = User.objects.create_user(
            username="admin",
            email="admin@chili.example",
            password="supersecret",
            is_staff=True,
        )
        self.product = Product.objects.create(
            name="Chilichip Kit",
            slug="chilichip-kit",
            description="Handheld kit.",
            price_cents=4999,
            stock=5,
            is_active=True,
        )
        self.hidden = Product.objects.create(
            name="Unreleased Board",
            slug="unreleased-board",
            price_cents=9999,
            stock=1,
            is_active=False,
        )

    def test_public_product_list_hides_inactive(self):
        response = self.client.get("/api/store/products/")
        self.assertEqual(response.status_code, 200)
        slugs = [row["slug"] for row in response.data["results"]]
        self.assertEqual(slugs, ["chilichip-kit"])

    def test_staff_can_create_product(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            "/api/store/products/",
            {
                "name": "D-pad Set",
                "description": "Replacement buttons.",
                "price_cents": 1299,
                "stock": 10,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["slug"], "d-pad-set")
        self.assertTrue(Product.objects.filter(slug="d-pad-set").exists())

    def test_non_staff_cannot_create_product(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/products/",
            {"name": "Nope", "price_cents": 100, "stock": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_checkout_requires_auth(self):
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    @patch("store.services.create_checkout_session")
    def test_checkout_creates_pending_order_and_reserves_stock(self, mock_create):
        mock_create.return_value = {
            "id": "cs_test_123",
            "url": "https://checkout.stripe.com/c/pay/cs_test_123",
        }
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.product.id, "quantity": 2}]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["session_id"], "cs_test_123")
        self.assertTrue(response.data["checkout_url"].startswith("https://checkout.stripe.com/"))
        order = Order.objects.get(pk=response.data["order"]["id"])
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.total_cents, 9998)
        self.assertEqual(order.items.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        params = mock_create.call_args.args[0]
        self.assertEqual(params["mode"], "payment")
        self.assertNotIn("payment_method_types", params)
        self.assertEqual(params["metadata"]["order_id"], str(order.id))
        self.assertIn("shipping_address_collection", params)

    def test_checkout_rejects_insufficient_stock(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.product.id, "quantity": 9}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_checkout_rejects_inactive_product(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.hidden.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("store.services.create_checkout_session")
    def test_stripe_failure_cancels_order_and_restores_stock(self, mock_create):
        mock_create.side_effect = StripeError("Stripe is down.", status_code=502)
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 502)
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_paid_webhook_marks_order_paid(self):
        self.client.force_authenticate(self.user)
        with patch(
            "store.services.create_checkout_session",
            return_value={
                "id": "cs_test_paid",
                "url": "https://checkout.stripe.com/c/pay/cs_test_paid",
            },
        ):
            checkout = self.client.post(
                "/api/store/checkout/",
                {"items": [{"product": self.product.id, "quantity": 1}]},
                format="json",
            )
        order_id = checkout.data["order"]["id"]
        payload = json.dumps(
            {
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_paid",
                        "status": "complete",
                        "payment_status": "paid",
                        "payment_intent": "pi_test_1",
                        "customer_details": {"email": "pepper@chili.example", "name": "Pepper"},
                        "shipping_details": {
                            "name": "Pepper",
                            "address": {
                                "line1": "1 Chili Ave",
                                "line2": "",
                                "city": "Austin",
                                "state": "TX",
                                "postal_code": "78701",
                                "country": "US",
                            },
                        },
                        "metadata": {"order_id": str(order_id)},
                    }
                },
            }
        ).encode("utf-8")
        response = self.client.post(
            "/api/store/stripe/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sign_webhook(payload),
        )
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.shipping_city, "Austin")
        self.assertEqual(order.stripe_payment_intent_id, "pi_test_1")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)

    def test_expired_webhook_restores_stock(self):
        self.client.force_authenticate(self.user)
        with patch(
            "store.services.create_checkout_session",
            return_value={
                "id": "cs_test_exp",
                "url": "https://checkout.stripe.com/c/pay/cs_test_exp",
            },
        ):
            checkout = self.client.post(
                "/api/store/checkout/",
                {"items": [{"product": self.product.id, "quantity": 2}]},
                format="json",
            )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        payload = json.dumps(
            {
                "type": "checkout.session.expired",
                "data": {
                    "object": {
                        "id": "cs_test_exp",
                        "status": "expired",
                        "payment_status": "unpaid",
                        "metadata": {"order_id": str(checkout.data["order"]["id"])},
                    }
                },
            }
        ).encode("utf-8")
        response = self.client.post(
            "/api/store/stripe/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sign_webhook(payload),
        )
        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(pk=checkout.data["order"]["id"])
        self.assertEqual(order.status, Order.Status.CANCELED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_invalid_webhook_signature_is_rejected(self):
        payload = b'{"type":"checkout.session.completed","data":{"object":{}}}'
        response = self.client.post(
            "/api/store/stripe/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
        )
        self.assertEqual(response.status_code, 400)

    @patch("store.services.retrieve_checkout_session")
    def test_confirm_syncs_paid_session(self, mock_retrieve):
        self.client.force_authenticate(self.user)
        with patch(
            "store.services.create_checkout_session",
            return_value={
                "id": "cs_test_confirm",
                "url": "https://checkout.stripe.com/c/pay/cs_test_confirm",
            },
        ):
            checkout = self.client.post(
                "/api/store/checkout/",
                {"items": [{"product": self.product.id, "quantity": 1}]},
                format="json",
            )
        mock_retrieve.return_value = {
            "id": "cs_test_confirm",
            "status": "complete",
            "payment_status": "paid",
            "payment_intent": "pi_confirm",
            "customer_details": {"email": "pepper@chili.example"},
            "shipping_details": {
                "name": "Pepper",
                "address": {
                    "line1": "2 Heat St",
                    "city": "Berlin",
                    "country": "DE",
                    "postal_code": "10115",
                    "state": "",
                    "line2": "",
                },
            },
            "metadata": {"order_id": str(checkout.data["order"]["id"])},
        }
        response = self.client.post(
            "/api/store/checkout/confirm/",
            {"session_id": "cs_test_confirm"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "paid")
        self.assertEqual(response.data["shipping_city"], "Berlin")

    def test_orders_are_scoped_to_the_buyer(self):
        other = User.objects.create_user(
            username="other",
            email="other@chili.example",
            password="supersecret",
        )
        mine = Order.objects.create(user=self.user, total_cents=100, currency="usd")
        Order.objects.create(user=other, total_cents=200, currency="usd")
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/store/orders/")
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, [mine.id])

    def test_flatten_params_encodes_nested_stripe_fields(self):
        items = flatten_params(
            {
                "line_items": [
                    {
                        "quantity": 1,
                        "price_data": {"currency": "usd", "unit_amount": 4999},
                    }
                ],
                "metadata": {"order_id": "1"},
            }
        )
        encoded = dict(items)
        self.assertEqual(encoded["line_items[0][quantity]"], "1")
        self.assertEqual(encoded["line_items[0][price_data][unit_amount]"], "4999")
        self.assertEqual(encoded["metadata[order_id]"], "1")

    def test_webhook_payload_verifies(self):
        payload = b'{"id":"evt_1"}'
        header = sign_webhook(payload)
        event = verify_webhook_payload(payload, header, WEBHOOK_SECRET)
        self.assertEqual(event["id"], "evt_1")
