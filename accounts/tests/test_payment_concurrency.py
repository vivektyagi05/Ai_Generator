"""
PHASE 4 Step 25/26 — concurrency tests for payment_service.

Same gating rationale as test_subscription_concurrency.py /
test_credit_concurrency.py / test_entitlement_concurrency.py: SQLite does
not meaningfully enforce select_for_update() row locking, so these are
skipped unless running against PostgreSQL (see pg_test_settings.py).
"""

import json
import threading
import unittest
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase

from accounts.models import Payment, Plan, ProviderEvent, Subscription
from accounts.services import payment_service


def _captured_payload(*, order_id, payment_id, amount):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id, "order_id": order_id,
                    "amount": amount, "currency": "INR",
                    "status": "captured", "method": "card",
                }
            }
        },
    }


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "select_for_update() row-locking is only meaningfully enforced on "
    "PostgreSQL in this project (SQLite silently ignores it). Run against "
    "a Postgres DATABASES config (see pg_test_settings.py) to actually "
    "verify.",
)
class PaymentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="race_payer", password="x")
        self.plan, _ = Plan.objects.get_or_create(
            code="PREMIUM", defaults={"name": "Premium", "display_order": 2}
        )
        from django.test import override_settings
        self._settings_ctx = override_settings(
            RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret"
        )
        self._settings_ctx.enable()

    def tearDown(self):
        self._settings_ctx.disable()

    def test_two_concurrent_create_order_calls_create_only_one_payment(self):
        """Step 25: two simultaneous order requests must not create two
        local Payment rows (uq_payment_one_created_per_user)."""
        barrier = threading.Barrier(2)
        errors = []
        order_counter = {"n": 0}
        lock = threading.Lock()

        def fake_create_order(**kwargs):
            with lock:
                order_counter["n"] += 1
                n = order_counter["n"]
            return {"id": f"order_race_{n}"}

        def attempt():
            try:
                barrier.wait(timeout=5)
                with patch(
                    "accounts.services.payment_service.razorpay_client.create_order",
                    side_effect=fake_create_order,
                ):
                    payment_service.create_payment_order(
                        user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # One of the two must have succeeded; the other either succeeded
        # by reusing the same row or raised DuplicateLiveSubscriptionError
        # -- never two independent CREATED Payment rows.
        created_count = Payment.objects.filter(user=self.user, status=Payment.Status.CREATED).count()
        self.assertEqual(created_count, 1)

    def test_two_concurrent_webhook_deliveries_same_event_id_activate_once(self):
        """Step 25: two simultaneous successful webhook deliveries for the
        SAME event_id must result in exactly one ProviderEvent row and
        exactly one subscription activation."""
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_concurrent_1"}
            payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

        payload = _captured_payload(order_id=payment.order_id, payment_id="pay_concurrent_1", amount=payment.amount)
        raw_body = json.dumps(payload).encode()

        barrier = threading.Barrier(2)
        errors = []

        def attempt():
            try:
                barrier.wait(timeout=5)
                payment_service.process_webhook_event(
                    event_id="evt_concurrent_1", event_type="payment.captured",
                    raw_body=raw_body, payload=payload,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(ProviderEvent.objects.filter(event_id="evt_concurrent_1").count(), 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CAPTURED)
        payment.subscription.refresh_from_db()
        self.assertEqual(payment.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(
            payment.subscription.audit_logs.filter(event_type="ACTIVATED").count(), 1
        )

    def test_two_concurrent_captured_events_different_ids_still_activate_once(self):
        """Step 25: two simultaneous, genuinely distinct webhook deliveries
        for the SAME underlying payment (different event_ids -- e.g.
        payment.captured delivered twice by Razorpay under different
        retries with different ids) must still result in exactly one
        subscription activation -- Payment.status/provider_event_id is the
        second backstop beyond ProviderEvent's own uniqueness."""
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_concurrent_2"}
            payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

        payload = _captured_payload(order_id=payment.order_id, payment_id="pay_concurrent_2", amount=payment.amount)
        raw_body_a = json.dumps(payload).encode()
        raw_body_b = json.dumps(payload).encode()

        barrier = threading.Barrier(2)
        errors = []

        def attempt(event_id, raw_body):
            try:
                barrier.wait(timeout=5)
                payment_service.process_webhook_event(
                    event_id=event_id, event_type="payment.captured", raw_body=raw_body, payload=payload,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt, args=("evt_a", raw_body_a))
        t2 = threading.Thread(target=attempt, args=("evt_b", raw_body_b))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CAPTURED)
        payment.subscription.refresh_from_db()
        self.assertEqual(
            payment.subscription.audit_logs.filter(event_type="ACTIVATED").count(), 1
        )

    def test_two_concurrent_checkout_verifications_do_not_double_authorize(self):
        """Step 25: two simultaneous verify-payment requests (e.g. a
        double-submitted browser callback) for the same order must not
        corrupt Payment state."""
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_concurrent_3"}
            payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

        barrier = threading.Barrier(2)
        errors = []

        def attempt():
            try:
                barrier.wait(timeout=5)
                payment_service.verify_checkout_callback(
                    user=self.user, order_id=payment.order_id, payment_id="pay_concurrent_3", signature="sig",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        # Patched once, outside both threads -- unittest.mock.patch's
        # start/stop is not safe to invoke concurrently from multiple
        # threads against the same target (one thread's __exit__ can
        # unpatch while the other is still inside the call), which is a
        # test-harness hazard, not something to work around in production
        # code. A single patch shared by both threads avoids that hazard
        # while still exercising real concurrent Payment row locking.
        with patch(
            "accounts.services.payment_service.razorpay_client.verify_checkout_signature",
            return_value=True,
        ), patch(
            "accounts.services.payment_service.razorpay_client.fetch_payment",
            return_value={
                "order_id": payment.order_id, "amount": payment.amount,
                "currency": payment.currency, "method": "card",
            },
        ):
            t1 = threading.Thread(target=attempt)
            t2 = threading.Thread(target=attempt)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(errors, [])
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.AUTHORIZED)
