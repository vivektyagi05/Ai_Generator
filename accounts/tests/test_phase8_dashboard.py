"""
PHASE 8 STEP 5 -- User Dashboard regression tests.

No dashboard route/view/template existed before this (confirmed by
grepping urls.py/views.py for "dashboard" before starting -- zero
matches). Covers:
  - the page requires login (like history/profile)
  - real credit/entitlement/generation data reaches the page (same
    services profile_data()/history_view() already use -- no second,
    divergent computation)
  - recent generations (ChatHistory) and recent activity (AIUsage,
    which -- unlike ChatHistory -- includes failures) are both scoped
    to request.user, never another user's rows
  - a failed AIUsage row is genuinely visible, not silently dropped
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import AIUsage, ChatHistory
from accounts.services import credit_service


class DashboardAccessTests(TestCase):
    def test_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class DashboardDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dashuser", password="pw12345!")
        self.other = User.objects.create_user(username="otheruser", password="pw12345!")
        self.client.force_login(self.user)

    def test_shows_real_credit_balance_not_a_placeholder(self):
        account = credit_service.get_or_create_account(user=self.user)
        starting_balance = credit_service.get_balance_snapshot(account)["available_balance"]
        credit_service.grant(account, amount=37, transaction_type="GRANT", reference_id="test-grant")
        response = self.client.get(reverse("dashboard"))
        expected = starting_balance + 37
        self.assertEqual(response.context["credits"]["available_balance"], expected)
        self.assertContains(response, str(expected))

    def test_shows_own_recent_generations_only(self):
        ChatHistory.objects.create(
            user=self.user, query="My own story", response="r", feature="story"
        )
        ChatHistory.objects.create(
            user=self.other, query="Someone else's story", response="r", feature="story"
        )
        response = self.client.get(reverse("dashboard"))
        titles = [item.query for item in response.context["recent_generations"]]
        self.assertIn("My own story", titles)
        self.assertNotIn("Someone else's story", titles)

    def test_shows_own_recent_activity_including_failures(self):
        AIUsage.objects.create(
            user=self.user,
            request_id="req-fail-1",
            feature="photo",
            provider="test",
            model="test",
            status=AIUsage.Status.PROVIDER_FAILURE,
        )
        AIUsage.objects.create(
            user=self.other,
            request_id="req-other-1",
            feature="photo",
            provider="test",
            model="test",
            status=AIUsage.Status.FINALIZED,
        )
        response = self.client.get(reverse("dashboard"))
        request_ids = [req.request_id for req in response.context["recent_requests"]]
        self.assertIn("req-fail-1", request_ids)
        self.assertNotIn("req-other-1", request_ids)
        # The failure must be genuinely visible on the page, not swallowed.
        self.assertContains(response, "Provider Failure")

    def test_generation_counts_are_a_real_groupby_not_hardcoded(self):
        ChatHistory.objects.create(user=self.user, query="a", response="r", feature="photo")
        ChatHistory.objects.create(user=self.user, query="b", response="r", feature="photo")
        ChatHistory.objects.create(user=self.user, query="c", response="r", feature="story")
        response = self.client.get(reverse("dashboard"))
        counts = {row["feature"]: row["count"] for row in response.context["generation_counts"]}
        self.assertEqual(counts["photo"], 2)
        self.assertEqual(counts["story"], 1)
        self.assertEqual(response.context["total_generations"], 3)

    def test_empty_state_when_no_generations(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "No generations yet")


class DashboardLapsedSubscriptionTests(TestCase):
    """
    PHASE 8B Rule 6 -- the dashboard had the same blindspot profile.html
    had before Item 2: a refunded user and a never-subscribed user both
    just show "Free" with no explanation. Reuses
    subscription_service.get_lapsed_subscription_summary (the same
    function profile_data calls), so this only needs to check it reaches
    dashboard_view's context and template correctly -- the underlying
    refunded-vs-expired distinction logic itself is already covered by
    ProfileDataLapsedSubscriptionTests.
    """

    def test_never_subscribed_user_sees_no_lapsed_notice(self):
        user = User.objects.create_user(username="dashneversubbed", password="pw12345!")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertIsNone(response.context["lapsed_subscription"])
        self.assertNotContains(response, "billing-status-banner")

    def test_refunded_user_sees_lapsed_notice_on_dashboard(self):
        import json
        from unittest.mock import patch
        from django.test import override_settings
        from accounts.services import payment_service

        user = User.objects.create_user(username="dashrefunded", password="pw12345!")
        with override_settings(
            RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret"
        ):
            with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
                mock_order.return_value = {"id": "order_dash_1"}
                payment, _ = payment_service.create_payment_order(
                    user=user, plan_code="PREMIUM", billing_interval="MONTHLY"
                )
            captured_payload = {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_dash_1",
                            "order_id": "order_dash_1",
                            "amount": payment.amount,
                            "currency": payment.currency,
                            "status": "captured",
                            "method": "card",
                        }
                    }
                },
            }
            payment_service.process_webhook_event(
                event_id="evt_dash_capture",
                event_type="payment.captured",
                raw_body=json.dumps(captured_payload).encode(),
                payload=captured_payload,
            )
            refund_payload = {
                "event": "refund.processed",
                "payload": {
                    "refund": {"entity": {"id": "rfnd_dash_1", "payment_id": "pay_dash_1", "amount": payment.amount}}
                },
            }
            payment_service.process_webhook_event(
                event_id="evt_dash_refund",
                event_type="refund.processed",
                raw_body=json.dumps(refund_payload).encode(),
                payload=refund_payload,
            )

        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertIsNotNone(response.context["lapsed_subscription"])
        self.assertEqual(response.context["lapsed_subscription"]["reason"], "refunded")
        self.assertContains(response, "billing-status-banner")
        self.assertContains(response, "refunded")

    def test_live_premium_user_sees_no_lapsed_notice(self):
        from accounts.models import Plan
        from accounts.services import subscription_service

        user = User.objects.create_user(username="dashlivepremium", password="pw12345!")
        plan = Plan.objects.get(code="PREMIUM")
        subscription = subscription_service.create_subscription(user=user, plan=plan, billing_interval="MONTHLY")
        subscription_service.activate_subscription(
            subscription=subscription, source="system:razorpay_webhook", reason="payment captured"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertIsNone(response.context["lapsed_subscription"])
        self.assertNotContains(response, "billing-status-banner")
