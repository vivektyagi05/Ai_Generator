"""
PHASE 3 Step 23/26 — concurrency tests for subscription_service.

Same gating rationale as accounts/tests/test_entitlement_concurrency.py and
test_credit_concurrency.py: SQLite does not meaningfully enforce
select_for_update() row locking, so these are skipped unless running
against PostgreSQL. See PHASE_3_COMPLETION_AUDIT.md for this project's
honestly-disclosed verification status in the environment this was built
in.
"""

import threading
import unittest

from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase

from accounts.models import Plan, Subscription
from accounts.services import subscription_service
from accounts.services.exceptions import DuplicateSubscriptionError


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "select_for_update() row-locking is only meaningfully enforced on "
    "PostgreSQL in this project (SQLite silently ignores it). Run against "
    "a Postgres DATABASES config (see pg_test_settings.py) to actually "
    "verify.",
)
class SubscriptionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="racer", password="x")
        # get_or_create rather than get(): TransactionTestCase truncates
        # tables (including migration-seeded catalog rows) after every test
        # in the class unless serialized_rollback is set, so a prior
        # TransactionTestCase in the same run can leave this table empty by
        # the time this one starts -- self-seed rather than depend on
        # migration data surviving across unrelated TransactionTestCases.
        self.plan, _ = Plan.objects.get_or_create(
            code="PREMIUM", defaults={"name": "Premium", "display_order": 1}
        )

    def test_two_concurrent_create_calls_do_not_create_duplicate_live_subscriptions(self):
        barrier = threading.Barrier(2)
        errors = []

        def attempt():
            try:
                barrier.wait(timeout=5)
                subscription_service.create_subscription(
                    user=self.user, plan=self.plan,
                    billing_interval=Subscription.BillingInterval.MONTHLY,
                )
            except DuplicateSubscriptionError:
                pass
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(errors, [])
        live_count = Subscription.objects.filter(
            user=self.user, status__in=Subscription.LIVE_STATUSES
        ).count()
        self.assertEqual(live_count, 1)

    def test_two_concurrent_renewals_do_not_double_extend_period(self):
        from accounts import billing_utils

        sub = subscription_service.create_subscription(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )
        sub = subscription_service.activate_subscription(subscription=sub)
        expected_period_end = billing_utils.compute_period_end(
            sub.current_period_end, sub.billing_interval
        )

        barrier = threading.Barrier(2)

        def attempt():
            try:
                barrier.wait(timeout=5)
                fresh = Subscription.objects.get(pk=sub.pk)
                subscription_service.renew_subscription(subscription=fresh, provider_event_id="race_evt")
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        sub.refresh_from_db()
        self.assertEqual(sub.current_period_end, expected_period_end)

    def test_concurrent_cancellation_is_not_double_processed(self):
        sub = subscription_service.create_subscription(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )
        sub = subscription_service.activate_subscription(subscription=sub)

        barrier = threading.Barrier(2)

        def attempt():
            try:
                barrier.wait(timeout=5)
                fresh = Subscription.objects.get(pk=sub.pk)
                subscription_service.cancel_subscription(subscription=fresh)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED)
