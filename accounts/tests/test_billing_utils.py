"""PHASE 3 Step 26 — billing-period math tests: monthly/yearly, leap years,
month-end clamping, timezone-aware boundaries."""

from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from accounts import billing_utils


def aware(*args):
    return timezone.make_aware(datetime(*args))


class BillingPeriodTests(TestCase):
    def test_monthly_ordinary(self):
        start = aware(2026, 3, 15)
        end = billing_utils.compute_period_end(start, billing_utils.MONTHLY)
        self.assertEqual(end, aware(2026, 4, 15))

    def test_monthly_from_jan_31_lands_on_feb_28_non_leap(self):
        start = aware(2026, 1, 31)
        end = billing_utils.compute_period_end(start, billing_utils.MONTHLY)
        self.assertEqual(end, aware(2026, 2, 28))

    def test_monthly_from_jan_31_lands_on_feb_29_leap_year(self):
        start = aware(2028, 1, 31)  # 2028 is a leap year
        end = billing_utils.compute_period_end(start, billing_utils.MONTHLY)
        self.assertEqual(end, aware(2028, 2, 29))

    def test_monthly_crosses_year_boundary(self):
        start = aware(2026, 12, 20)
        end = billing_utils.compute_period_end(start, billing_utils.MONTHLY)
        self.assertEqual(end, aware(2027, 1, 20))

    def test_yearly_ordinary(self):
        start = aware(2026, 8, 14)
        end = billing_utils.compute_period_end(start, billing_utils.YEARLY)
        self.assertEqual(end, aware(2027, 8, 14))

    def test_yearly_from_leap_day_clamps_on_non_leap_target(self):
        start = aware(2028, 2, 29)
        end = billing_utils.compute_period_end(start, billing_utils.YEARLY)
        self.assertEqual(end, aware(2029, 2, 28))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(billing_utils.NaiveDatetimeError):
            billing_utils.compute_period_end(datetime(2026, 1, 1), billing_utils.MONTHLY)

    def test_unknown_interval_rejected(self):
        with self.assertRaises(ValueError):
            billing_utils.compute_period_end(aware(2026, 1, 1), "WEEKLY")

    def test_grace_period_end(self):
        entered = aware(2026, 1, 1)
        deadline = billing_utils.grace_period_end(entered, grace_days=7)
        self.assertEqual(deadline, aware(2026, 1, 8))

    def test_renewal_chain_does_not_drift_across_months_with_varying_lengths(self):
        # Jan 31 -> Feb 28 -> Mar 28 (not back to 31st) -- renewals always
        # advance from the previous period's own end, so this is expected
        # and documented behavior, not drift.
        start = aware(2026, 1, 31)
        p1 = billing_utils.compute_period_end(start, billing_utils.MONTHLY)
        p2 = billing_utils.compute_period_end(p1, billing_utils.MONTHLY)
        self.assertEqual(p1, aware(2026, 2, 28))
        self.assertEqual(p2, aware(2026, 3, 28))
