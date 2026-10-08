from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from store.models import Category, DeliveryOption, Order, Product, ProductImage
from store.stripe import StripeError, flatten_params, verify_webhook_payload

User = get_user_model()

WEBHOOK_SECRET = "whsec_test_secret"

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def sign_webhook(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time() if timestamp is None else timestamp)
    signed = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


@override_settings(
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET,
    STRIPE_SYNC_ENABLED=False,
)
class StoreApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password="supersecret",
            email_verified=True,
        )
        self.staff = User.objects.create_user(
            username="admin",
            email="admin@chili.example",
            password="supersecret",
            email_verified=True,
            is_staff=True,
        )
        self.product = Product.objects.create(
            name="Chilichip Kit",
            slug="chilichip-kit",
            short_description="Handheld kit.",
            long_description="## Kit\n\n- Board\n- Buttons",
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
                "short_description": "Replacement buttons.",
                "long_description": "A **set** of spare d-pad parts.",
                "price_cents": 1299,
                "stock": 10,
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["slug"], "d-pad-set")
        self.assertEqual(response.data["short_description"], "Replacement buttons.")
        self.assertIn("**set**", response.data["long_description"])
        self.assertTrue(Product.objects.filter(slug="d-pad-set").exists())

    def test_public_product_detail_includes_markdown_copy(self):
        response = self.client.get("/api/store/products/chilichip-kit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["short_description"], "Handheld kit.")
        self.assertIn("## Kit", response.data["long_description"])
        self.assertNotIn("description", response.data)
        self.assertEqual(response.data["images"], [])
        self.assertEqual(response.data["image_url"], "")

    def test_product_images_are_listed_in_sort_order(self):
        ProductImage.objects.create(
            product=self.product,
            image=SimpleUploadedFile("second.png", TINY_PNG, content_type="image/png"),
            alt="Back",
            sort_order=2,
        )
        first = ProductImage.objects.create(
            product=self.product,
            image=SimpleUploadedFile("first.png", TINY_PNG, content_type="image/png"),
            alt="Front",
            sort_order=1,
        )
        response = self.client.get("/api/store/products/chilichip-kit/")
        self.assertEqual(response.status_code, 200)
        alts = [row["alt"] for row in response.data["images"]]
        self.assertEqual(alts, ["Front", "Back"])
        self.assertEqual(response.data["image_url"], response.data["images"][0]["url"])
        self.assertIn("/media/", response.data["images"][0]["url"])
        media = self.client.get(first.image.url)
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media["Content-Type"], "image/png")
        self.assertEqual(b"".join(media.streaming_content), TINY_PNG)

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
        self.assertEqual(order.shipping_status, Order.ShippingStatus.AWAITING_PAYMENT)
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

    @patch("store.views.create_order_checkout", side_effect=RuntimeError("d1 exploded"))
    def test_checkout_unexpected_error_returns_json_not_html(self, _mock_checkout):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b"<html", response.content.lower())
        self.assertEqual(response.json()["detail"], "Something went wrong. Try again in a moment.")

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
                        "collected_information": {
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
                            }
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
        self.assertEqual(order.shipping_status, Order.ShippingStatus.PREPARING)
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
        self.assertEqual(order.shipping_status, Order.ShippingStatus.NOT_SHIPPING)
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
            "collected_information": {
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
                }
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

    @patch("store.services.retrieve_checkout_session")
    def test_order_retrieve_backfills_collected_shipping_address(self, mock_retrieve):
        self.client.force_authenticate(self.user)
        order = Order.objects.create(
            user=self.user,
            status=Order.Status.PAID,
            total_cents=4999,
            currency="eur",
            stripe_checkout_session_id="cs_test_hydrate",
            shipping_name="Pepper",
        )
        mock_retrieve.return_value = {
            "id": "cs_test_hydrate",
            "collected_information": {
                "shipping_details": {
                    "name": "Pepper",
                    "address": {
                        "line1": "9 Basil Rd",
                        "city": "Krakow",
                        "country": "PL",
                        "postal_code": "30-001",
                        "state": "",
                        "line2": "",
                    },
                }
            },
        }
        response = self.client.get(f"/api/store/orders/{order.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["shipping_line1"], "9 Basil Rd")
        self.assertEqual(response.data["shipping_city"], "Krakow")
        self.assertEqual(response.data["shipping_country"], "PL")
        order.refresh_from_db()
        self.assertEqual(order.shipping_line1, "9 Basil Rd")

    def test_orders_are_scoped_to_the_buyer(self):
        other = User.objects.create_user(
            username="other",
            email="other@chili.example",
            password="supersecret",
            email_verified=True,
        )
        mine = Order.objects.create(user=self.user, total_cents=100, currency="eur")
        Order.objects.create(user=other, total_cents=200, currency="eur")
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
                        "price_data": {"currency": "eur", "unit_amount": 4999},
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

    @patch("store.services.create_checkout_session")
    def test_checkout_uses_synced_stripe_price(self, mock_create):
        mock_create.return_value = {
            "id": "cs_test_price",
            "url": "https://checkout.stripe.com/c/pay/cs_test_price",
        }
        self.product.stripe_price_id = "price_kit"
        self.product.save(update_fields=["stripe_price_id"])
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/checkout/",
            {"items": [{"product": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        params = mock_create.call_args.args[0]
        self.assertEqual(params["line_items"][0]["price"], "price_kit")
        self.assertIn("payment_intent_data", params)

    @override_settings(STRIPE_SYNC_ENABLED=True, STRIPE_SECRET_KEY="sk_test_123")
    @patch("store.sync.create_price", return_value={"id": "price_1"})
    @patch("store.sync.create_product", return_value={"id": "prod_1"})
    def test_sync_product_creates_stripe_catalog(self, _product, _price):
        from store.sync import sync_product_to_stripe

        product = sync_product_to_stripe(self.product)
        self.assertEqual(product.stripe_product_id, "prod_1")
        self.assertEqual(product.stripe_price_id, "price_1")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stripe_product_id, "prod_1")

    @patch("store.sync.sync_order_to_stripe")
    def test_set_shipping_status_marks_paid_order_shipped(self, _sync):
        from store.sync import set_shipping_status

        order = Order.objects.create(
            user=self.user,
            status=Order.Status.PAID,
            shipping_status=Order.ShippingStatus.PREPARING,
            total_cents=4999,
        )
        set_shipping_status(order, Order.ShippingStatus.SHIPPED)
        order.refresh_from_db()
        self.assertEqual(order.shipping_status, Order.ShippingStatus.SHIPPED)
        self.assertEqual(order.status, Order.Status.FULFILLED)


class StoreCatalogFilterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username="admin",
            email="admin@chili.example",
            password="supersecret",
            email_verified=True,
            is_staff=True,
        )
        self.pcbs = Category.objects.create(name="PCBs", slug="pcbs", sort_order=1)
        self.modules = Category.objects.create(name="MCUs & modules", slug="modules", sort_order=2)
        self.empty = Category.objects.create(name="Accessories", slug="accessories", sort_order=3)
        Product.objects.create(
            name="PCB v1.1.4",
            slug="pcb-v1-1-4",
            sku="PCB-114",
            category=self.pcbs,
            price_cents=1999,
            stock=3,
        )
        Product.objects.create(
            name="RP2350 Plus",
            slug="rp2350-plus",
            sku="RP2350",
            short_description="Dual-core microcontroller board.",
            category=self.modules,
            price_cents=899,
            stock=0,
        )
        Product.objects.create(
            name="Sticker pack",
            slug="sticker-pack",
            price_cents=300,
            stock=10,
        )
        Product.objects.create(
            name="Hidden PCB",
            slug="hidden-pcb",
            category=self.pcbs,
            price_cents=500,
            stock=1,
            is_active=False,
        )

    def slugs(self, query: str = "") -> list[str]:
        response = self.client.get(f"/api/store/products/{query}")
        self.assertEqual(response.status_code, 200, response.data)
        return [row["slug"] for row in response.data["results"]]

    def test_categories_list_counts_active_products(self):
        response = self.client.get("/api/store/categories/")
        self.assertEqual(response.status_code, 200)
        rows = {row["slug"]: row["product_count"] for row in response.data}
        self.assertEqual(rows, {"pcbs": 1, "modules": 1, "accessories": 0})
        self.assertEqual([row["slug"] for row in response.data], ["pcbs", "modules", "accessories"])

    def test_staff_category_count_includes_inactive(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/store/categories/pcbs/")
        self.assertEqual(response.data["product_count"], 2)

    def test_non_staff_cannot_create_category(self):
        response = self.client.post("/api/store/categories/", {"name": "Tools"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_staff_can_create_category_with_generated_slug(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post("/api/store/categories/", {"name": "Shells & parts"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["slug"], "shells-parts")

    def test_product_includes_category(self):
        response = self.client.get("/api/store/products/pcb-v1-1-4/")
        self.assertEqual(response.data["category"], "pcbs")
        self.assertEqual(response.data["category_name"], "PCBs")

    def test_staff_can_assign_category(self):
        self.client.force_authenticate(self.staff)
        response = self.client.patch(
            "/api/store/products/sticker-pack/",
            {"category": "accessories"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Product.objects.get(slug="sticker-pack").category, self.empty)

    def test_filter_by_category(self):
        self.assertEqual(self.slugs("?category=pcbs"), ["pcb-v1-1-4"])
        self.assertEqual(self.slugs("?category=none"), ["sticker-pack"])
        self.assertEqual(self.slugs("?category=missing"), [])

    def test_search_matches_name_sku_copy_and_category(self):
        self.assertEqual(self.slugs("?search=rp2350"), ["rp2350-plus"])
        self.assertEqual(self.slugs("?search=PCB-114"), ["pcb-v1-1-4"])
        self.assertEqual(self.slugs("?search=dual-core"), ["rp2350-plus"])
        self.assertEqual(self.slugs("?search=mcus"), ["rp2350-plus"])
        self.assertEqual(self.slugs("?search=nothing+matches"), [])

    def test_search_does_not_reveal_inactive_products(self):
        self.assertEqual(self.slugs("?search=hidden"), [])

    def test_price_and_stock_filters(self):
        self.assertEqual(self.slugs("?min_price_cents=500&max_price_cents=1000"), ["rp2350-plus"])
        self.assertEqual(self.slugs("?in_stock=true"), ["pcb-v1-1-4", "sticker-pack"])

    def test_invalid_price_is_rejected(self):
        response = self.client.get("/api/store/products/?min_price_cents=cheap")
        self.assertEqual(response.status_code, 400)

    def test_ordering(self):
        self.assertEqual(
            self.slugs("?ordering=price"),
            ["sticker-pack", "rp2350-plus", "pcb-v1-1-4"],
        )
        self.assertEqual(
            self.slugs("?ordering=-price"),
            ["pcb-v1-1-4", "rp2350-plus", "sticker-pack"],
        )

    def test_deleting_category_keeps_products(self):
        self.pcbs.delete()
        self.assertIsNone(Product.objects.get(slug="pcb-v1-1-4").category)


CHECKOUT_SESSION = {"id": "cs_test_del", "url": "https://checkout.stripe.com/c/pay/cs_test_del"}


@override_settings(
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_SYNC_ENABLED=False,
    STORE_SHIPPING_COUNTRIES=["DE", "PL", "FR"],
)
class DeliveryOptionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password="supersecret",
            email_verified=True,
        )
        self.staff = User.objects.create_user(
            username="admin",
            email="admin@chili.example",
            password="supersecret",
            email_verified=True,
            is_staff=True,
        )
        self.product = Product.objects.create(
            name="Chilichip Kit", slug="chilichip-kit", price_cents=4999, stock=5
        )
        self.parcel = DeliveryOption.objects.create(
            name="EU parcel",
            slug="eu-parcel",
            estimate="3-7 business days",
            price_cents=599,
            free_over_cents=7500,
            sort_order=1,
        )
        self.courier = DeliveryOption.objects.create(
            name="Courier", slug="courier", price_cents=1499, countries="pl", sort_order=2
        )
        self.pickup = DeliveryOption.objects.create(
            name="Local pickup", slug="pickup", price_cents=0, requires_address=False, sort_order=3
        )
        self.retired = DeliveryOption.objects.create(
            name="Old post", slug="old-post", price_cents=100, is_active=False
        )

    def checkout(self, quantity: int = 1, delivery: str | None = "eu-parcel"):
        self.client.force_authenticate(self.user)
        payload: dict = {"items": [{"product": self.product.id, "quantity": quantity}]}
        if delivery is not None:
            payload["delivery_option"] = delivery
        return self.client.post("/api/store/checkout/", payload, format="json")

    def test_public_list_hides_disabled_options(self):
        response = self.client.get("/api/store/delivery-options/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["slug"] for row in response.data], ["eu-parcel", "courier", "pickup"]
        )
        self.assertEqual(response.data[1]["countries"], ["PL"])
        self.assertEqual(response.data[0]["free_over_cents"], 7500)

    def test_staff_sees_disabled_options(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get("/api/store/delivery-options/")
        self.assertIn("old-post", [row["slug"] for row in response.data])

    def test_non_staff_cannot_create_option(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/store/delivery-options/", {"name": "Drone", "price_cents": 1}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_can_create_and_disable_option(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            "/api/store/delivery-options/",
            {"name": "Express DE", "price_cents": 1999, "countries": ["de", "fr"]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["slug"], "express-de")
        self.assertEqual(response.data["countries"], ["DE", "FR"])
        response = self.client.patch(
            "/api/store/delivery-options/express-de/", {"is_active": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DeliveryOption.objects.get(slug="express-de").is_active)

    def test_staff_cannot_add_country_the_store_does_not_ship_to(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            "/api/store/delivery-options/",
            {"name": "US", "price_cents": 1999, "countries": ["US"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("store.services.create_checkout_session", return_value=CHECKOUT_SESSION)
    def test_checkout_adds_delivery_fee_to_total_and_stripe(self, mock_create):
        response = self.checkout()
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data["order"]["id"])
        self.assertEqual(order.delivery_option, self.parcel)
        self.assertEqual(order.delivery_name, "EU parcel")
        self.assertEqual(order.delivery_cents, 599)
        self.assertEqual(order.total_cents, 4999 + 599)
        self.assertEqual(response.data["order"]["items_cents"], 4999)
        params = mock_create.call_args.args[0]
        rate = params["shipping_options"][0]["shipping_rate_data"]
        self.assertEqual(rate["fixed_amount"], {"amount": 599, "currency": "eur"})
        self.assertEqual(rate["display_name"], "EU parcel")
        self.assertEqual(
            params["shipping_address_collection"]["allowed_countries"], ["DE", "PL", "FR"]
        )

    @patch("store.services.create_checkout_session", return_value=CHECKOUT_SESSION)
    def test_free_delivery_over_threshold(self, mock_create):
        response = self.checkout(quantity=2)
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data["order"]["id"])
        self.assertEqual(order.delivery_cents, 0)
        self.assertEqual(order.total_cents, 9998)
        rate = mock_create.call_args.args[0]["shipping_options"][0]["shipping_rate_data"]
        self.assertEqual(rate["fixed_amount"]["amount"], 0)

    @patch("store.services.create_checkout_session", return_value=CHECKOUT_SESSION)
    def test_country_limited_option_limits_address_countries(self, mock_create):
        self.assertEqual(self.checkout(delivery="courier").status_code, 201)
        params = mock_create.call_args.args[0]
        self.assertEqual(params["shipping_address_collection"]["allowed_countries"], ["PL"])

    @patch("store.services.create_checkout_session", return_value=CHECKOUT_SESSION)
    def test_pickup_skips_address_collection(self, mock_create):
        self.assertEqual(self.checkout(delivery="pickup").status_code, 201)
        self.assertNotIn("shipping_address_collection", mock_create.call_args.args[0])

    def test_checkout_requires_option_when_options_exist(self):
        response = self.checkout(delivery=None)
        self.assertEqual(response.status_code, 400)
        self.assertIn("delivery_option", response.data)
        self.assertEqual(Order.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_checkout_rejects_disabled_option(self):
        response = self.checkout(delivery="old-post")
        self.assertEqual(response.status_code, 400)
        self.assertIn("delivery_option", response.data)

    @patch("store.services.create_checkout_session", return_value=CHECKOUT_SESSION)
    def test_checkout_without_any_options_keeps_free_shipping(self, mock_create):
        DeliveryOption.objects.update(is_active=False)
        response = self.checkout(delivery=None)
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data["order"]["id"])
        self.assertIsNone(order.delivery_option)
        self.assertEqual(order.total_cents, 4999)
        params = mock_create.call_args.args[0]
        self.assertNotIn("shipping_options", params)
        self.assertIn("shipping_address_collection", params)
