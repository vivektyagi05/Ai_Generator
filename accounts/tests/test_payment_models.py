"""PHASE 4 Step 27 — Payment / ProviderEvent model constraint tests."""

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Payment, Plan, ProviderEvent, Subscription


class PaymentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="payer", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")

    def _make_payment(self, **overrides):
        defaults = dict(
            user=self.user,
            plan=self.plan,
            billing_interval=Subscription.BillingInterval.MONTHLY,
            amount=29900,
            currency="INR",
            status=Payment.Status.CREATED,
        )
        defaults.update(overrides)
        return Payment.objects.create(**defaults)

    def test_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_payment(amount=0)

    def test_negative_amount_rejected(self):
        with self.assertRaises((IntegrityError, ValueError)):
            with transaction.atomic():
                self._make_payment(amount=-100)

    def test_only_one_created_payment_per_user(self):
        self._make_payment()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_payment()

    def test_second_created_payment_allowed_for_different_user(self):
        self._make_payment()
        other = User.objects.create_user(username="payer2", password="x")
        # Should not raise -- constraint is scoped per-user.
        self._make_payment(user=other)

    def test_two_created_payments_allowed_if_first_resolved(self):
        p1 = self._make_payment()
        p1.status = Payment.Status.CAPTURED
        p1.save(update_fields=["status"])
        # Now a second CREATED payment for the same user is fine.
        self._make_payment()

    def test_duplicate_order_id_rejected(self):
        self._make_payment(order_id="order_abc")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_payment(status=Payment.Status.FAILED, order_id="order_abc")

    def test_duplicate_payment_id_rejected(self):
        self._make_payment(status=Payment.Status.CAPTURED, payment_id="pay_abc")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_payment(status=Payment.Status.FAILED, payment_id="pay_abc")

    def test_null_order_id_does_not_collide(self):
        # Multiple payments with order_id=None must not trip the unique
        # constraint (it's conditioned on order_id__isnull=False).
        self._make_payment()
        p2 = self._make_payment(status=Payment.Status.FAILED)
        self.assertIsNone(p2.order_id)

    def test_invalid_status_rejected_at_db_level(self):
        payment = Payment(
            user=self.user, plan=self.plan, billing_interval="MONTHLY",
            amount=100, status="NOT_A_REAL_STATUS",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                payment.save()

    def test_invalid_billing_interval_rejected(self):
        payment = Payment(
            user=self.user, plan=self.plan, billing_interval="WEEKLY",
            amount=100, status=Payment.Status.CREATED,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                payment.save()

    def test_str_does_not_crash(self):
        payment = self._make_payment()
        self.assertIn("Payment(", str(payment))


class ProviderEventModelTests(TestCase):
    def _make_event(self, **overrides):
        defaults = dict(
            provider="razorpay",
            event_id="evt_123",
            event_type="payment.captured",
            payload_hash="a" * 64,
        )
        defaults.update(overrides)
        return ProviderEvent.objects.create(**defaults)

    def test_duplicate_event_id_rejected(self):
        self._make_event()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_event()

    def test_same_event_id_different_provider_allowed(self):
        self._make_event(provider="razorpay")
        self._make_event(provider="other_provider")

    def test_default_status_is_received(self):
        event = self._make_event()
        self.assertEqual(event.status, ProviderEvent.Status.RECEIVED)

    def test_str_does_not_crash(self):
        event = self._make_event()
        self.assertIn("ProviderEvent(", str(event))
