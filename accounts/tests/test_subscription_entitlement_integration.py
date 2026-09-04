"""PHASE 3 Step 26 — Subscription <-> UserEntitlement integration tests."""

from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import Plan, Subscription, UserEntitlement
from accounts.services import entitlement_service, subscription_service


class SubscriptionEntitlementIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="carol", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")

    def _create(self):
        return subscription_service.create_subscription(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )

    def test_activation_creates_premium_entitlement(self):
        sub = self._create()
        subscription_service.activate_subscription(subscription=sub)
        self.assertEqual(entitlement_service.get_current_plan(self.user, None), "PREMIUM")

    def test_activation_links_entitlement_to_subscription(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        self.assertIsNotNone(sub.entitlement)
        self.assertEqual(sub.entitlement.source, f"subscription:{sub.pk}")
        self.assertEqual(sub.entitlement.expires_at, sub.current_period_end)

    def test_duplicate_activation_does_not_create_duplicate_entitlement(self):
        sub = self._create()
        subscription_service.activate_subscription(subscription=sub)
        subscription_service.activate_subscription(subscription=sub)  # idempotent no-op
        count = UserEntitlement.objects.filter(
            user=self.user, plan=self.plan, status=UserEntitlement.Status.ACTIVE
        ).count()
        self.assertEqual(count, 1)

    def test_immediate_cancellation_falls_back_to_free(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        subscription_service.cancel_subscription(subscription=sub)
        self.assertEqual(entitlement_service.get_current_plan(self.user, None), "FREE")

    def test_expiration_falls_back_to_free(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        subscription_service.expire_subscription(subscription=sub)
        self.assertEqual(entitlement_service.get_current_plan(self.user, None), "FREE")

    def test_renewal_extends_entitlement_expiry(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        first_expiry = sub.entitlement.expires_at
        sub = subscription_service.renew_subscription(subscription=sub)
        sub.entitlement.refresh_from_db()
        self.assertGreater(sub.entitlement.expires_at, first_expiry)
        self.assertEqual(sub.entitlement.expires_at, sub.current_period_end)

    def test_scheduled_cancellation_keeps_premium_access_until_period_end(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        subscription_service.schedule_cancellation(subscription=sub)
        # Entitlement is untouched -- still active until current_period_end.
        self.assertEqual(entitlement_service.get_current_plan(self.user, None), "PREMIUM")
