from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from games.models import Game
from marketplace.fees import split_price
from marketplace.models import ConnectedAccount, Earning, Listing, Payout, Purchase
from marketplace.payments import (
    assert_platform_charge,
    assert_recipient_account,
    recipient_account_body,
)
from store.stripe import StripeError

User = get_user_model()
WEBHOOK_SECRET = "whsec_test_secret"


def sign_webhook(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time() if timestamp is None else timestamp)
    signed = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def account_payload(transfers: str = "active", payouts: str = "active", account_id: str = "acct_test") -> dict:
    return {
        "id": account_id,
        "object": "v2.core.account",
        "dashboard": "none",
        "configuration": {
            "recipient": {
                "capabilities": {
                    "stripe_balance": {
                        "stripe_transfers": {"status": transfers},
                        "payouts": {"status": payouts},
                    }
                }
            }
        },
    }


def forbid_charge_keys(params: dict) -> None:
    assert_platform_charge(params)
    assert "payment_method_types" not in params
    assert "application_fee_amount" not in json.dumps(params)


@override_settings(
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_PUBLISHABLE_KEY="pk_test_123",
    STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET,
    STRIPE_SYNC_ENABLED=False,
)
class MarketplaceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(
            username="pepper",
            email="pepper@chili.example",
            password="supersecret",
        )
        self.buyer = User.objects.create_user(
            username="sage",
            email="sage@chili.example",
            password="supersecret",
        )
        self.staff = User.objects.create_user(
            username="admin",
            email="admin@chili.example",
            password="supersecret",
            is_staff=True,
        )
        self.game = Game.objects.create(owner=self.seller, title="Moss Maze", data="room 0")
        self.other_game = Game.objects.create(owner=self.buyer, title="Not Mine", data="room 1")

    def _list(self, user, game, price_cents, **extra):
        self.client.force_authenticate(user)
        payload = {
            "game": game.id,
            "price_cents": price_cents,
            "category": extra.pop("category", "puzzle"),
            "description": extra.pop("description", "A tiny maze."),
            "tags": extra.pop("tags", ["bitsy", "maze"]),
            **extra,
        }
        response = self.client.post("/api/marketplace/listings/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response

    def _paid_webhook(self, purchase_id: int, session_id: str, payment_intent: str = "pi_game") -> None:
        payload = json.dumps(
            {
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": session_id,
                        "status": "complete",
                        "payment_status": "paid",
                        "payment_intent": {
                            "id": payment_intent,
                            "latest_charge": "ch_game",
                        },
                        "metadata": {
                            "kind": "marketplace_purchase",
                            "purchase_id": str(purchase_id),
                        },
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
        self.assertEqual(response.status_code, 200, response.content)

    def _clear_hold(self, purchase: Purchase) -> Earning:
        earning = purchase.earning
        earning.available_at = timezone.now() - timedelta(days=1)
        earning.save(update_fields=["available_at", "updated_at"])
        return earning

    def test_fee_split_keeps_twenty_percent_and_processing_estimate(self):
        split = split_price(1000)
        self.assertEqual(split["platform_fee_cents"], 200)
        self.assertEqual(split["processing_estimate_cents"], 59)
        self.assertEqual(split["creator_credit_cents"], 741)
        self.assertEqual(split_price(0)["creator_credit_cents"], 0)
        minimum = split_price(100)
        self.assertEqual(minimum["platform_fee_cents"], 20)
        self.assertGreater(minimum["creator_credit_cents"], 0)

    def test_connected_account_is_a_v2_recipient_without_a_dashboard(self):
        body = recipient_account_body(self.seller)
        assert_recipient_account(body)
        self.assertEqual(body["dashboard"], "none")
        self.assertNotIn("type", body)
        self.assertNotIn("merchant", body["configuration"])
        self.assertNotIn("card_payments", json.dumps(body["configuration"]))
        transfers = body["configuration"]["recipient"]["capabilities"]["stripe_balance"]["stripe_transfers"]
        self.assertTrue(transfers["requested"])
        self.assertNotIn("payouts", body["configuration"]["recipient"]["capabilities"]["stripe_balance"])
        self.assertEqual(body["defaults"]["responsibilities"]["fees_collector"], "application")
        self.assertEqual(body["defaults"]["responsibilities"]["losses_collector"], "application")

    def test_categories_are_seeded(self):
        response = self.client.get("/api/marketplace/categories/")
        self.assertEqual(response.status_code, 200)
        slugs = {row["slug"] for row in response.json()}
        self.assertIn("puzzle", slugs)
        self.assertIn("narrative", slugs)

    def test_config_exposes_minimums_and_publishable_key(self):
        response = self.client.get("/api/marketplace/config/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["min_paid_cents"], 100)
        self.assertEqual(body["min_payout_cents"], 2000)
        self.assertEqual(body["hold_days"], 7)
        self.assertEqual(body["platform_fee_bps"], 2000)
        self.assertEqual(body["stripe_publishable_key"], "pk_test_123")

    def test_paid_price_below_one_dollar_is_rejected_and_free_is_allowed(self):
        self.client.force_authenticate(self.seller)
        response = self.client.post(
            "/api/marketplace/listings/",
            {"game": self.game.id, "price_cents": 50, "category": "puzzle"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        free = self._list(self.seller, self.game, 0)
        self.assertEqual(free.json()["price_cents"], 0)
        self.assertEqual(free.json()["game"]["owner"], "pepper")
        self.assertEqual(free.json()["tags"], ["bitsy", "maze"])

    def test_seller_cannot_list_someone_elses_game(self):
        self.client.force_authenticate(self.seller)
        response = self.client.post(
            "/api/marketplace/listings/",
            {"game": self.other_game.id, "price_cents": 100, "category": "puzzle"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_search_tags_and_price_filters(self):
        self._list(self.seller, self.game, 500, tags=["bitsy"], description="Mossy corridors")
        other = Game.objects.create(owner=self.seller, title="Sky Duel", data="room 2")
        self._list(
            self.seller,
            other,
            0,
            category="action",
            tags=["duel"],
            description="A short fight.",
        )
        self.client.force_authenticate(user=None)
        search = self.client.get("/api/marketplace/listings/", {"q": "moss"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["count"], 1)
        self.assertEqual(search.json()["results"][0]["slug"], "moss-maze")
        free = self.client.get("/api/marketplace/listings/", {"price": "free"})
        self.assertEqual(free.json()["count"], 1)
        tagged = self.client.get("/api/marketplace/listings/", {"tag": "bitsy"})
        self.assertEqual(tagged.json()["count"], 1)
        action = self.client.get("/api/marketplace/listings/", {"category": "action"})
        self.assertEqual(action.json()["results"][0]["game"]["title"], "Sky Duel")
        tags = self.client.get("/api/marketplace/tags/")
        names = {row["name"] for row in tags.json()}
        self.assertIn("bitsy", names)

    @patch("marketplace.payments.create_checkout_session")
    def test_paid_checkout_charges_chili_and_webhook_credits_the_creator(self, mock_checkout):
        mock_checkout.return_value = {
            "id": "cs_game_1",
            "url": "https://checkout.stripe.com/c/pay/cs_game_1",
        }
        self._list(self.seller, self.game, 1000)
        self.client.force_authenticate(self.buyer)
        response = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["session_id"], "cs_game_1")
        self.assertFalse(response.json()["free"])
        params = mock_checkout.call_args.args[0]
        forbid_charge_keys(params)
        self.assertEqual(params["metadata"]["kind"], "marketplace_purchase")
        self.assertNotIn("shipping_address_collection", params)
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.status, Purchase.Status.PENDING)
        self.assertEqual(purchase.platform_fee_cents, 200)
        self.assertEqual(purchase.processing_estimate_cents, 59)
        self.assertEqual(purchase.creator_credit_cents, 741)
        self.assertFalse(ConnectedAccount.objects.filter(user=self.seller).exists())

        self._paid_webhook(purchase.id, "cs_game_1")
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.PAID)
        self.assertEqual(purchase.stripe_charge_id, "ch_game")
        earning = purchase.earning
        self.assertEqual(earning.status, Earning.Status.HELD)
        self.assertEqual(earning.unpaid_cents, 741)
        self.assertGreater(earning.available_at, timezone.now() + timedelta(days=6))

        self.client.force_authenticate(self.seller)
        sales = self.client.get("/api/marketplace/me/")
        self.assertEqual(sales.status_code, 200, sales.content)
        self.assertEqual(sales.json()["balance"]["held_cents"], 741)
        self.assertEqual(sales.json()["balance"]["available_cents"], 0)
        self.assertEqual(sales.json()["payout"]["blocked_reason"], "Earnings are in the 7-day hold.")
        self.assertFalse(sales.json()["payout"]["transferred"])
        self.assertEqual(sales.json()["sales"][0]["creator_credit_cents"], 741)

    def test_free_claim_does_not_charge_a_card(self):
        self._list(self.seller, self.game, 0)
        self.client.force_authenticate(self.buyer)
        with patch("marketplace.payments.create_checkout_session") as mock_checkout:
            response = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["free"])
        mock_checkout.assert_not_called()
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.status, Purchase.Status.PAID)
        self.assertEqual(purchase.price_cents, 0)
        self.assertFalse(Earning.objects.exists())
        library = self.client.get("/api/marketplace/library/")
        self.assertEqual(library.json()["count"], 1)
        self.assertEqual(library.json()["results"][0]["title"], "Moss Maze")

    def test_buyer_cannot_purchase_their_own_game_or_buy_twice(self):
        self._list(self.seller, self.game, 0)
        self.client.force_authenticate(self.seller)
        own = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        self.assertEqual(own.status_code, 400)
        self.client.force_authenticate(self.buyer)
        self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        again = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(Purchase.objects.filter(status=Purchase.Status.PAID).count(), 1)

    @patch("marketplace.services.retrieve_connected_account")
    @patch("marketplace.services.send_transfer")
    def test_payout_waits_for_setup_hold_and_twenty_dollars(self, mock_transfer, mock_account):
        mock_account.return_value = account_payload(transfers="inactive", payouts="inactive")
        mock_transfer.return_value = {"id": "tr_game_1"}
        self._list(self.seller, self.game, 1000)
        self.client.force_authenticate(self.buyer)
        with patch(
            "marketplace.payments.create_checkout_session",
            return_value={"id": "cs_hold", "url": "https://checkout.stripe.com/c/pay/cs_hold"},
        ):
            checkout = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        purchase = Purchase.objects.get(pk=checkout.json()["purchase"]["id"])
        self._paid_webhook(purchase.id, "cs_hold")

        self.client.force_authenticate(self.seller)
        early = self.client.post("/api/marketplace/me/payouts/", format="json")
        self.assertFalse(early.json()["transferred"])
        self.assertEqual(early.json()["blocked_reason"], "Earnings are in the 7-day hold.")
        self.assertEqual(early.json()["balance"]["held_cents"], 741)
        mock_transfer.assert_not_called()

        self._clear_hold(purchase)
        cleared = self.client.post("/api/marketplace/me/payouts/", format="json")
        self.assertEqual(cleared.json()["balance"]["available_cents"], 741)
        self.assertEqual(cleared.json()["blocked_reason"], "Connect payouts before Chili can send earnings.")
        mock_transfer.assert_not_called()

        with patch(
            "marketplace.services.create_connected_account",
            return_value=account_payload(transfers="inactive", payouts="inactive"),
        ):
            created = self.client.post("/api/marketplace/me/account/", format="json")
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["transfers_status"], "inactive")
        blocked = self.client.post("/api/marketplace/me/payouts/", format="json")
        self.assertEqual(blocked.json()["blocked_reason"], "Transfers are not active.")
        mock_transfer.assert_not_called()

        mock_account.return_value = account_payload()
        still_short = self.client.post("/api/marketplace/me/payouts/", format="json")
        self.assertEqual(still_short.json()["blocked_reason"], "Cleared earnings are under $20.")
        mock_transfer.assert_not_called()

        for index, title in enumerate(("Second", "Third"), start=2):
            game = Game.objects.create(owner=self.seller, title=title, data=f"room {index}")
            self._list(self.seller, game, 1000, tags=[f"tag{index}"])
            self.client.force_authenticate(self.buyer)
            with patch(
                "marketplace.payments.create_checkout_session",
                return_value={
                    "id": f"cs_more_{index}",
                    "url": f"https://checkout.stripe.com/c/pay/cs_more_{index}",
                },
            ):
                more = self.client.post(
                    f"/api/marketplace/listings/{game.slug}/checkout/",
                    format="json",
                )
            self._paid_webhook(more.json()["purchase"]["id"], f"cs_more_{index}", f"pi_more_{index}")
            extra = Purchase.objects.get(pk=more.json()["purchase"]["id"])
            self._clear_hold(extra)

        self.client.force_authenticate(self.seller)
        paid = self.client.post("/api/marketplace/me/payouts/", format="json")
        self.assertTrue(paid.json()["transferred"], paid.content)
        self.assertEqual(paid.json()["amount_cents"], 741 * 3)
        self.assertGreaterEqual(paid.json()["amount_cents"], 2000)
        transfer = mock_transfer.call_args.kwargs
        self.assertEqual(transfer["amount"], 741 * 3)
        self.assertEqual(transfer["currency"], "usd")
        self.assertEqual(transfer["destination"], "acct_test")
        self.assertNotIn("application_fee_amount", transfer)
        self.assertEqual(Payout.objects.get().amount_cents, 741 * 3)
        self.assertEqual(Earning.objects.filter(status=Earning.Status.PAID).count(), 3)
        self.assertEqual(paid.json()["balance"]["available_cents"], 0)

    @patch("marketplace.services.retrieve_connected_account", return_value=account_payload())
    @patch("marketplace.services.reverse_transfer")
    @patch("marketplace.services.send_transfer")
    def test_refund_reduces_unpaid_earnings_and_reverses_a_sent_transfer(
        self,
        mock_transfer,
        mock_reversal,
        _mock_account,
    ):
        mock_transfer.return_value = {"id": "tr_refund"}
        self._list(self.seller, self.game, 2700)
        self.client.force_authenticate(self.buyer)
        with patch(
            "marketplace.payments.create_checkout_session",
            return_value={"id": "cs_refund", "url": "https://checkout.stripe.com/c/pay/cs_refund"},
        ):
            checkout = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        purchase = Purchase.objects.get(pk=checkout.json()["purchase"]["id"])
        self._paid_webhook(purchase.id, "cs_refund", "pi_refund")
        self._clear_hold(purchase)
        credit = purchase.creator_credit_cents
        self.assertGreaterEqual(credit, 2000)

        with patch("marketplace.services.create_connected_account", return_value=account_payload()):
            self.client.force_authenticate(self.seller)
            self.client.post("/api/marketplace/me/account/", format="json")
        # Keep the earning unpaid for the first refund path by using a second purchase.
        unpaid = Purchase.objects.get(pk=purchase.id)
        # Claw back before payout.
        from marketplace.services import apply_money_adjustment

        apply_money_adjustment(unpaid, refunded_cents=unpaid.price_cents)
        unpaid.refresh_from_db()
        unpaid.earning.refresh_from_db()
        self.assertEqual(unpaid.status, Purchase.Status.REFUNDED)
        self.assertEqual(unpaid.earning.unpaid_cents, 0)
        self.assertEqual(unpaid.earning.status, Earning.Status.REVERSED)
        mock_reversal.assert_not_called()

        game = Game.objects.create(owner=self.seller, title="Paid Out", data="room 9")
        self._list(self.seller, game, 2700, tags=["later"])
        self.client.force_authenticate(self.buyer)
        with patch(
            "marketplace.payments.create_checkout_session",
            return_value={"id": "cs_paidout", "url": "https://checkout.stripe.com/c/pay/cs_paidout"},
        ):
            second = self.client.post(f"/api/marketplace/listings/{game.slug}/checkout/", format="json")
        sold = Purchase.objects.get(pk=second.json()["purchase"]["id"])
        self._paid_webhook(sold.id, "cs_paidout", "pi_paidout")
        self._clear_hold(sold)
        self.client.force_authenticate(self.seller)
        payout = self.client.post("/api/marketplace/me/payouts/", format="json")
        self.assertTrue(payout.json()["transferred"], payout.content)

        self.client.force_authenticate(self.staff)
        with patch("marketplace.services.refund_payment_intent", return_value={"id": "re_1"}):
            refund = self.client.post(f"/api/marketplace/purchases/{sold.id}/refund/", format="json")
        self.assertEqual(refund.status_code, 200, refund.content)
        self.assertEqual(refund.json()["status"], "refunded")
        mock_reversal.assert_called_once()
        self.assertEqual(mock_reversal.call_args.kwargs["transfer_id"], "tr_refund")
        self.assertEqual(mock_reversal.call_args.kwargs["amount"], sold.creator_credit_cents)
        sold.earning.refresh_from_db()
        self.assertEqual(sold.earning.settled_cents, 0)

    @patch("marketplace.services.retrieve_connected_account", return_value=account_payload())
    @patch("marketplace.services.reverse_transfer")
    @patch("marketplace.services.send_transfer", return_value={"id": "tr_dispute"})
    def test_dispute_reverses_a_transfer_and_a_win_restores_earnings(
        self,
        _mock_transfer,
        mock_reversal,
        _mock_account,
    ):
        self._list(self.seller, self.game, 2700)
        self.client.force_authenticate(self.buyer)
        with patch(
            "marketplace.payments.create_checkout_session",
            return_value={"id": "cs_dp", "url": "https://checkout.stripe.com/c/pay/cs_dp"},
        ):
            checkout = self.client.post("/api/marketplace/listings/moss-maze/checkout/", format="json")
        purchase = Purchase.objects.get(pk=checkout.json()["purchase"]["id"])
        self._paid_webhook(purchase.id, "cs_dp", "pi_dp")
        self._clear_hold(purchase)
        with patch("marketplace.services.create_connected_account", return_value=account_payload()):
            self.client.force_authenticate(self.seller)
            self.client.post("/api/marketplace/me/account/", format="json")
        self.client.post("/api/marketplace/me/payouts/", format="json")
        credit = purchase.creator_credit_cents

        dispute = json.dumps(
            {
                "type": "charge.dispute.created",
                "data": {
                    "object": {
                        "id": "dp_1",
                        "object": "dispute",
                        "amount": 2700,
                        "status": "needs_response",
                        "charge": "ch_game",
                        "payment_intent": "pi_dp",
                    }
                },
            }
        ).encode("utf-8")
        response = self.client.post(
            "/api/store/stripe/webhook/",
            data=dispute,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sign_webhook(dispute),
        )
        self.assertEqual(response.status_code, 200, response.content)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DISPUTED)
        mock_reversal.assert_called_once()
        self.assertEqual(mock_reversal.call_args.kwargs["amount"], credit)

        won = json.dumps(
            {
                "type": "charge.dispute.closed",
                "data": {
                    "object": {
                        "id": "dp_1",
                        "object": "dispute",
                        "amount": 2700,
                        "status": "won",
                        "charge": "ch_game",
                        "payment_intent": "pi_dp",
                    }
                },
            }
        ).encode("utf-8")
        won_response = self.client.post(
            "/api/store/stripe/webhook/",
            data=won,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sign_webhook(won),
        )
        self.assertEqual(won_response.status_code, 200, won_response.content)
        purchase.refresh_from_db()
        purchase.earning.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.PAID)
        self.assertEqual(purchase.earning.unpaid_cents, credit)
        self.assertEqual(purchase.earning.status, Earning.Status.AVAILABLE)

    @patch("marketplace.payments.create_account_session")
    def test_account_session_enables_embedded_components_without_a_stripe_login(self, mock_session):
        mock_session.return_value = {"client_secret": "accs_secret_test"}
        ConnectedAccount.objects.create(
            user=self.seller,
            stripe_account_id="acct_test",
            transfers_status="inactive",
            payouts_status="inactive",
        )
        self.client.force_authenticate(self.seller)
        response = self.client.post("/api/marketplace/me/account-session/", format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["client_secret"], "accs_secret_test")
        params = mock_session.call_args.args[0]
        self.assertEqual(
            set(params["components"]),
            {"account_onboarding", "notification_banner", "account_management", "payouts"},
        )
        for component in params["components"].values():
            self.assertTrue(component["enabled"])
            self.assertTrue(component["features"]["disable_stripe_user_authentication"])
        self.assertNotIn("payments", params["components"])

    def test_store_checkout_webhook_still_marks_orders(self):
        from store.models import Order, Product

        product = Product.objects.create(
            name="Kit",
            sku="CHIP-KIT",
            price_cents=4999,
            stock=2,
            is_active=True,
        )
        order = Order.objects.create(
            user=self.buyer,
            currency="usd",
            total_cents=4999,
            stripe_checkout_session_id="cs_kit",
        )
        payload = json.dumps(
            {
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_kit",
                        "status": "complete",
                        "payment_status": "paid",
                        "payment_intent": "pi_kit",
                        "metadata": {"order_id": str(order.id)},
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
        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertFalse(Purchase.objects.exists())
        product.refresh_from_db()
