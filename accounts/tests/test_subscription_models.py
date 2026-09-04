"""PHASE 3 Step 26 — Subscription model tests: choices, constraints, and
the one-live-subscription-per-user uniqueness rule."""

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import Plan, Subscription


class SubscriptionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")

    def test_create_pending_subscription(self):
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )
        self.assertEqual(sub.status, Subscription.Status.PENDING)
        self.assertFalse(sub.cancel_at_period_end)
        self.assertIsNone(sub.current_period_end)

    def test_duplicate_live_subscription_for_same_user_rejected(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
            status=Subscription.Status.ACTIVE,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subscription.objects.create(
                    user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
                    status=Subscription.Status.PENDING,
                )

    def test_terminal_subscriptions_do_not_block_a_new_live_one(self):
        Subscription.objects.create(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
            status=Subscription.Status.CANCELLED, ended_at=timezone.now(),
        )
        # A second, terminal row for the same user is fine...
        Subscription.objects.create(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
            status=Subscription.Status.EXPIRED, ended_at=timezone.now(),
        )
        # ...and so is a brand new live one.
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
        )
        self.assertEqual(sub.status, Subscription.Status.PENDING)

    def test_period_end_before_start_rejected(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subscription.objects.create(
                    user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
                    status=Subscription.Status.ACTIVE,
                    current_period_start=now,
                    current_period_end=now - timezone.timedelta(days=1),
                )

    def test_is_period_expired(self):
        now = timezone.now()
        sub = Subscription.objects.create(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY,
            status=Subscription.Status.ACTIVE,
            current_period_start=now - timezone.timedelta(days=40),
            current_period_end=now - timezone.timedelta(days=10),
        )
        self.assertTrue(sub.is_period_expired())
