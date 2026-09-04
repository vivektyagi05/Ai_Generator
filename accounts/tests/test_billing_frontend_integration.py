"""
PHASE 4B — frontend billing integration tests.

Covers the seam this phase actually added/changed:

  * templates/plans.html (accounts.views.plans_page) — real server-resolved
    pricing, and CTA state that differs correctly by anonymous/FREE/PREMIUM.
  * accounts.views.profile_data now also returning a "credits" snapshot,
    reusing accounts.services.credit_service (Step 10 — no second credit
    system).
  * accounts.views.user_login's new "next" return-URL handling (Step 13),
    including that it rejects an external/open-redirect target.

Does not re-test the Phase 4 payment backend itself (create-order/
verify-payment/webhook) -- see accounts/tests/test_billing_views.py and
accounts/tests/test_webhook_views.py, both left untouched and still
passing.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase

from accounts.models import Plan, Subscription
from accounts.services import subscription_service


class PlansPagePricingTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_anonymous_sees_real_price_and_login_cta(self):
        response = self.client.get("/plans/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Log in to upgrade", html)
        self.assertNotIn("Coming soon", html)
        # price must come from billing_config, not be absent/hardcoded blank
        self.assertIn("\u20b9", html)  # rupee sign renders somewhere for the Premium card

    def test_free_user_sees_upgrade_cta_not_premium_active(self):
        user = User.objects.create_user(username="freeuser", password="x")
        self.client.force_login(user)
        response = self.client.get("/plans/")
        html = response.content.decode()
        self.assertIn("Upgrade securely", html)
        self.assertNotIn("Premium Active", html)

    def test_premium_user_sees_premium_active_not_duplicate_upgrade(self):
        user = User.objects.create_user(username="premuser", password="x")
        plan = Plan.objects.get(code="PREMIUM")
        sub = subscription_service.create_subscription(
            user=user, plan=plan, billing_interval="MONTHLY", performed_by=user, source="test"
        )
        subscription_service.activate_subscription(subscription=sub, performed_by=user, source="test")

        self.client.force_login(user)
        response = self.client.get("/plans/")
        html = response.content.decode()
        self.assertIn("Premium Active", html)
        # The FREE card's own CTA text ("Your current plan"/"Included") is
        # fine either way; what matters is the Premium card doesn't offer a
        # second purchase button for someone already Premium.
        self.assertNotIn('id="upgradeBtn-PREMIUM"', html)

    def test_pricing_matches_billing_config(self):
        from accounts import billing_config

        expected_amount, _currency = billing_config.resolve_price("PREMIUM", "MONTHLY")
        response = self.client.get("/plans/")
        html = response.content.decode()
        expected_display = f"\u20b9{expected_amount / 100:,.0f}"
        self.assertIn(expected_display, html)


class ProfileDataCreditsTests(TestCase):
    def test_profile_data_includes_credit_snapshot(self):
        user = User.objects.create_user(username="creditcheck", password="x")
        client = Client()
        client.force_login(user)

        response = client.get("/profile/data/")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertIn("credits", body)
        for key in ("balance", "reserved_balance", "available_balance", "lifetime_earned", "lifetime_used"):
            self.assertIn(key, body["credits"])
        self.assertIn("entitlement", body)
        self.assertIn("subscription", body)

    def test_profile_data_credits_match_credit_service(self):
        from accounts.services import credit_service

        user = User.objects.create_user(username="creditmatch", password="x")
        account = credit_service.get_or_create_account(user=user)
        expected = credit_service.get_balance_snapshot(account)

        client = Client()
        client.force_login(user)
        body = client.get("/profile/data/").json()

        self.assertEqual(body["credits"]["balance"], expected["balance"])
        self.assertEqual(body["credits"]["lifetime_earned"], expected["lifetime_earned"])

    def test_profile_data_requires_login(self):
        client = Client()
        response = client.get("/profile/data/")
        self.assertNotEqual(response.status_code, 200)


class ProfileDataLapsedSubscriptionTests(TestCase):
    """PHASE 8B Rule 5 — a refunded subscription must be distinguishable
    from a user who was simply never Premium, or an ordinary (non-refund)
    expiry."""

    def test_never_subscribed_user_has_no_lapsed_subscription(self):
        user = User.objects.create_user(username="neversubbed", password="x")
        client = Client()
        client.force_login(user)
        body = client.get("/profile/data/").json()
        self.assertIn("lapsed_subscription", body)
        self.assertIsNone(body["lapsed_subscription"])

    def test_refunded_subscription_reported_as_refunded(self):
        import json
        from unittest.mock import patch
        from django.test import override_settings
        from accounts.services import payment_service

        user = User.objects.create_user(username="refundedprofile", password="x")
        with override_settings(
            RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret"
        ):
            with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
                mock_order.return_value = {"id": "order_lapsed_1"}
                payment, _ = payment_service.create_payment_order(
                    user=user, plan_code="PREMIUM", billing_interval="MONTHLY"
                )
            captured_payload = {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_lapsed_1",
                            "order_id": "order_lapsed_1",
                            "amount": payment.amount,
                            "currency": payment.currency,
                            "status": "captured",
                            "method": "card",
                        }
                    }
                },
            }
            payment_service.process_webhook_event(
                event_id="evt_lapsed_capture",
                event_type="payment.captured",
                raw_body=json.dumps(captured_payload).encode(),
                payload=captured_payload,
            )
            refund_payload = {
                "event": "refund.processed",
                "payload": {
                    "refund": {"entity": {"id": "rfnd_lapsed_1", "payment_id": "pay_lapsed_1", "amount": payment.amount}}
                },
            }
            payment_service.process_webhook_event(
                event_id="evt_lapsed_refund",
                event_type="refund.processed",
                raw_body=json.dumps(refund_payload).encode(),
                payload=refund_payload,
            )

        client = Client()
        client.force_login(user)
        body = client.get("/profile/data/").json()

        self.assertIsNotNone(body["lapsed_subscription"])
        self.assertEqual(body["lapsed_subscription"]["reason"], "refunded")
        self.assertEqual(body["lapsed_subscription"]["plan"], "PREMIUM")
        self.assertIsNotNone(body["lapsed_subscription"]["ended_at"])
        # And the live subscription itself is correctly gone/FREE.
        self.assertEqual(body["subscription"]["plan"], "FREE")

    def test_ordinary_expiry_not_reported_as_refunded(self):
        from django.utils import timezone

        user = User.objects.create_user(username="ordinaryexpiry", password="x")
        plan = Plan.objects.get(code="PREMIUM")
        subscription = subscription_service.create_subscription(user=user, plan=plan, billing_interval="MONTHLY")
        subscription_service.activate_subscription(
            subscription=subscription, source="system:razorpay_webhook", reason="payment captured"
        )
        subscription_service.expire_subscription(
            subscription=subscription, source="system:expiration_sync", reason="subscription period ended"
        )

        client = Client()
        client.force_login(user)
        body = client.get("/profile/data/").json()

        self.assertIsNotNone(body["lapsed_subscription"])
        self.assertEqual(body["lapsed_subscription"]["reason"], "expired")

    def test_live_subscription_has_no_lapsed_subscription(self):
        user = User.objects.create_user(username="livepremium", password="x")
        plan = Plan.objects.get(code="PREMIUM")
        subscription = subscription_service.create_subscription(user=user, plan=plan, billing_interval="MONTHLY")
        subscription_service.activate_subscription(
            subscription=subscription, source="system:razorpay_webhook", reason="payment captured"
        )

        client = Client()
        client.force_login(user)
        body = client.get("/profile/data/").json()

        self.assertIsNone(body["lapsed_subscription"])
        self.assertEqual(body["subscription"]["status"], "ACTIVE")


class LoginReturnUrlTests(TestCase):
    """Phase 13: preserve the anonymous visitor's intended destination
    (e.g. /plans/) across login, without enabling an open redirect."""

    def setUp(self):
        self.user = User.objects.create_user(username="returnuser", password="correct-pass")
        self.client = Client()

    def test_login_redirects_to_safe_next(self):
        response = self.client.post(
            "/login/",
            {"email": "returnuser", "password": "correct-pass", "next": "/plans/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/plans/")

    def test_login_rejects_external_redirect_target(self):
        response = self.client.post(
            "/login/",
            {"email": "returnuser", "password": "correct-pass", "next": "https://evil.example.com/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, "https://evil.example.com/")

    def test_login_without_next_still_goes_home(self):
        response = self.client.post(
            "/login/",
            {"email": "returnuser", "password": "correct-pass"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/home/")
