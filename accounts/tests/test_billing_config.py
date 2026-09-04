"""PHASE 4 — billing_config.py: server-side price catalog tests."""

from django.test import TestCase

from accounts import billing_config


class BillingConfigTests(TestCase):
    def test_resolve_price_premium_monthly(self):
        amount, currency = billing_config.resolve_price("PREMIUM", "MONTHLY")
        self.assertGreater(amount, 0)
        self.assertIsInstance(amount, int)
        self.assertEqual(currency, "INR")

    def test_resolve_price_premium_yearly(self):
        amount, currency = billing_config.resolve_price("PREMIUM", "YEARLY")
        self.assertGreater(amount, 0)
        self.assertEqual(currency, "INR")

    def test_free_plan_is_not_payable(self):
        self.assertFalse(billing_config.is_payable_plan("FREE"))
        with self.assertRaises(billing_config.UnpayablePlanError):
            billing_config.resolve_price("FREE", "MONTHLY")

    def test_unknown_plan_is_not_payable(self):
        self.assertFalse(billing_config.is_payable_plan("NOT_A_REAL_PLAN"))
        with self.assertRaises(billing_config.UnpayablePlanError):
            billing_config.resolve_price("NOT_A_REAL_PLAN", "MONTHLY")

    def test_unknown_billing_interval_is_not_payable(self):
        with self.assertRaises(billing_config.UnpayablePlanError):
            billing_config.resolve_price("PREMIUM", "WEEKLY")

    def test_amount_is_integer_minor_units_not_float(self):
        amount, _ = billing_config.resolve_price("PREMIUM", "MONTHLY")
        self.assertIsInstance(amount, int)
        self.assertNotIsInstance(amount, float)

    def test_premium_is_payable(self):
        self.assertTrue(billing_config.is_payable_plan("PREMIUM"))
