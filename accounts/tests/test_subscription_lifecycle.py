"""PHASE 3 Step 26 — subscription lifecycle / state-machine tests."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from accounts.models import Plan, Subscription
from accounts.services import subscription_service
from accounts.services.exceptions import DuplicateSubscriptionError, InvalidTransitionError


class SubscriptionLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bob", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")

    def _create(self):
        return subscription_service.create_subscription(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )

    def test_pending_to_active(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(sub.current_period_start)
        self.assertIsNotNone(sub.current_period_end)

    def test_second_live_subscription_for_same_user_rejected(self):
        self._create()
        with self.assertRaises(DuplicateSubscriptionError):
            self._create()

    def test_active_to_past_due_to_grace_to_expired(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.mark_past_due(subscription=sub)
        self.assertEqual(sub.status, Subscription.Status.PAST_DUE)
        sub = subscription_service.enter_grace_period(subscription=sub, grace_days=3)
        self.assertEqual(sub.status, Subscription.Status.GRACE_PERIOD)
        sub = subscription_service.expire_subscription(subscription=sub)
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
        self.assertIsNotNone(sub.ended_at)

    def test_past_due_can_restore_to_active_via_activate(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.mark_past_due(subscription=sub)
        sub = subscription_service.activate_subscription(subscription=sub, reason="payment recovered")
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_active_to_cancelled_immediate(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.cancel_subscription(subscription=sub)
        self.assertEqual(sub.status, Subscription.Status.CANCELLED)
        self.assertIsNotNone(sub.ended_at)

    def test_cancellation_at_period_end_stays_active_until_period_end(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.schedule_cancellation(subscription=sub)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertTrue(sub.cancel_at_period_end)
        # Effective status stays ACTIVE until the period actually ends.
        self.assertEqual(subscription_service.get_effective_status(sub), Subscription.Status.ACTIVE)

    def test_restore_reverses_scheduled_cancellation(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.schedule_cancellation(subscription=sub)
        sub = subscription_service.restore_subscription(subscription=sub)
        self.assertFalse(sub.cancel_at_period_end)
        self.assertIsNone(sub.cancelled_at)

    def test_cancelled_to_expired_allowed(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.cancel_subscription(subscription=sub)
        sub = subscription_service.expire_subscription(subscription=sub)
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)

    def test_expired_to_active_not_allowed(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        sub = subscription_service.expire_subscription(subscription=sub)
        with self.assertRaises(InvalidTransitionError):
            subscription_service.activate_subscription(subscription=sub)

    def test_sync_subscription_expires_a_lapsed_period(self):
        sub = self._create()
        sub = subscription_service.activate_subscription(subscription=sub)
        Subscription.objects.filter(pk=sub.pk).update(
            current_period_start=timezone.now() - timezone.timedelta(days=40),
            current_period_end=timezone.now() - timezone.timedelta(days=1),
        )
        sub.refresh_from_db()
        sub = subscription_service.sync_subscription(sub)
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
