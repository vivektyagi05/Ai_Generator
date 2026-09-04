"""PHASE 3 Step 25/26 — subscription API security tests: no client-
controlled plan/status/expiry, no activate endpoint, no cross-user access."""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from accounts.models import Plan, Subscription
from accounts.services import subscription_service


class SubscriptionApiSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="eve2", password="x")
        self.other = User.objects.create_user(username="mallory", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")

    def test_anonymous_cannot_read_subscription(self):
        response = self.client.get("/api/subscription/")
        self.assertNotEqual(response.status_code, 200)

    def test_free_user_gets_free_fallback_shape(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/subscription/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["plan"], "FREE")
        self.assertIsNone(data["billing_interval"])
        self.assertFalse(data["cancel_at_period_end"])

    def test_no_activate_endpoint_exists(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/subscription/activate/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_client_cannot_grant_premium_via_cancel_endpoint_payload(self):
        self.client.force_login(self.user)
        # No live subscription exists yet -- attempting to "cancel" one with
        # a forged body must never create/activate anything.
        response = self.client.post(
            "/api/subscription/cancel/",
            data=json.dumps({"plan": "PREMIUM", "status": "ACTIVE", "expires_at": "2099-01-01T00:00:00Z"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(subscription_service.__name__ and Subscription.objects.filter(user=self.user).count(), 0)

    def test_cross_user_subscription_not_visible(self):
        sub = subscription_service.create_subscription(
            user=self.other, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )
        subscription_service.activate_subscription(subscription=sub)

        self.client.force_login(self.user)
        response = self.client.get("/api/subscription/")
        data = response.json()
        # Requesting user has no subscription of their own -- must never see
        # `other`'s PREMIUM state, and no subscription id is exposed at all.
        self.assertEqual(data["plan"], "FREE")
        self.assertNotIn("id", data)
        self.assertNotIn("user_id", data)

    def test_cross_user_cannot_cancel_others_subscription(self):
        sub = subscription_service.create_subscription(
            user=self.other, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )
        subscription_service.activate_subscription(subscription=sub)

        self.client.force_login(self.user)
        response = self.client.post(
            "/api/subscription/cancel/", data=json.dumps({"immediate": True}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 404)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_response_never_exposes_internal_pk_or_provider_fields(self):
        sub = subscription_service.create_subscription(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
            provider="razorpay", provider_subscription_id="sub_secret_123",
        )
        subscription_service.activate_subscription(subscription=sub)

        self.client.force_login(self.user)
        response = self.client.get("/api/subscription/")
        body = response.content.decode()
        self.assertNotIn("sub_secret_123", body)
        self.assertNotIn("provider", body)
