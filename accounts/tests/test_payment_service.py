"""
PHASE 4 Step 27 — accounts/services/payment_service.py tests.

Mocks only accounts.services.razorpay_client's outbound functions (the
network/crypto boundary) -- everything else (local Payment/Subscription
state machine transitions, idempotency, amount verification) runs for
real against the test database.
"""

import hashlib
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from accounts import billing_config
from accounts.models import Payment, Plan, ProviderEvent, Subscription, UserEntitlement
from accounts.services import payment_service
from accounts.services.exceptions import (
    DuplicateLiveSubscriptionError,
    PaymentNotFoundError,
    PaymentVerificationError,
    UnpayablePlanError,
)
from accounts.services.razorpay_client import SignatureVerificationFailed


def _captured_payload(*, order_id, payment_id, amount, currency="INR", method="card"):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": amount,
                    "currency": currency,
                    "status": "captured",
                    "method": method,
                }
            }
        },
    }


def _failed_payload(*, order_id, payment_id, reason="card declined"):
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "error_description": reason,
                }
            }
        },
    }


def _refund_payload(*, payment_id, amount, refund_id="rfnd_1"):
    # Real Razorpay refund.processed payloads key the refund's own id and
    # amount under payload.refund.entity -- no order_id anywhere in this
    # entity, only payment_id (Step: refund entities are looked up by
    # payment_id, not order_id, see _resolve_payment_for_refund_webhook).
    return {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "payment_id": payment_id,
                    "amount": amount,
                    "currency": "INR",
                    "status": "processed",
                }
            }
        },
    }


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class CreatePaymentOrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_creates_local_payment_and_pending_subscription(self, mock_create_order):
        mock_create_order.return_value = {"id": "order_abc"}
        payment, checkout_info = payment_service.create_payment_order(
            user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
        )
        self.assertEqual(payment.status, Payment.Status.CREATED)
        self.assertEqual(payment.order_id, "order_abc")
        self.assertEqual(payment.subscription.status, Subscription.Status.PENDING)
        self.assertEqual(checkout_info["order_id"], "order_abc")
        self.assertNotIn("key_secret", checkout_info)

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_amount_is_server_resolved_never_from_caller(self, mock_create_order):
        mock_create_order.return_value = {"id": "order_abc"}
        payment, checkout_info = payment_service.create_payment_order(
            user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
        )
        expected_amount, expected_currency = billing_config.resolve_price("PREMIUM", "MONTHLY")
        self.assertEqual(payment.amount, expected_amount)
        self.assertEqual(checkout_info["amount"], expected_amount)
        self.assertEqual(checkout_info["currency"], expected_currency)

    def test_unpayable_plan_rejected_before_touching_razorpay(self):
        with self.assertRaises(UnpayablePlanError):
            payment_service.create_payment_order(
                user=self.user, plan_code="FREE", billing_interval="MONTHLY"
            )

    def test_invalid_billing_interval_rejected(self):
        with self.assertRaises(UnpayablePlanError):
            payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="WEEKLY"
            )

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_double_click_reuses_existing_created_payment(self, mock_create_order):
        mock_create_order.return_value = {"id": "order_abc"}
        payment1, info1 = payment_service.create_payment_order(
            user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
        )
        payment2, info2 = payment_service.create_payment_order(
            user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
        )
        self.assertEqual(payment1.pk, payment2.pk)
        # Razorpay order.create should only have been called once -- the
        # second call reused the existing in-flight order (Step 9).
        self.assertEqual(mock_create_order.call_count, 1)

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_conflicting_in_flight_payment_for_different_plan_rejected(self, mock_create_order):
        mock_create_order.return_value = {"id": "order_abc"}
        payment_service.create_payment_order(user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY")
        with self.assertRaises(DuplicateLiveSubscriptionError):
            payment_service.create_payment_order(user=self.user, plan_code="PREMIUM", billing_interval="YEARLY")

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_renewal_uses_existing_active_subscription_not_a_new_one(self, mock_create_order):
        mock_create_order.side_effect = [{"id": "order_1"}, {"id": "order_2"}]
        payment1, _ = payment_service.create_payment_order(
            user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
        )
        from accounts.services import subscription_service
        subscription_service.activate_subscription(subscription=payment1.subscription)
        payment1.status = Payment.Status.CAPTURED
        payment1.save(update_fields=["status"])

        payment2, _ = payment_service.create_payment_order(
            user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
        )
        self.assertEqual(payment2.subscription_id, payment1.subscription_id)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class VerifyCheckoutCallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer2", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_abc"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

    def test_unknown_order_id_raises_not_found(self):
        with self.assertRaises(PaymentNotFoundError):
            payment_service.verify_checkout_callback(
                user=self.user, order_id="order_does_not_exist", payment_id="pay_1", signature="sig"
            )

    def test_other_users_order_id_raises_not_found_not_leaked(self):
        other = User.objects.create_user(username="mallory2", password="x")
        with self.assertRaises(PaymentNotFoundError):
            payment_service.verify_checkout_callback(
                user=other, order_id=self.payment.order_id, payment_id="pay_1", signature="sig"
            )

    @patch("accounts.services.payment_service.razorpay_client.verify_checkout_signature")
    def test_invalid_signature_does_not_authorize(self, mock_verify):
        mock_verify.side_effect = SignatureVerificationFailed("bad sig")
        with self.assertRaises(SignatureVerificationFailed):
            payment_service.verify_checkout_callback(
                user=self.user, order_id=self.payment.order_id, payment_id="pay_1", signature="bad"
            )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    @patch("accounts.services.payment_service.razorpay_client.fetch_payment")
    @patch("accounts.services.payment_service.razorpay_client.verify_checkout_signature")
    def test_amount_mismatch_from_remote_rejected(self, mock_verify, mock_fetch):
        mock_verify.return_value = True
        mock_fetch.return_value = {
            "order_id": self.payment.order_id, "amount": 1, "currency": "INR", "method": "card",
        }
        with self.assertRaises(PaymentVerificationError):
            payment_service.verify_checkout_callback(
                user=self.user, order_id=self.payment.order_id, payment_id="pay_1", signature="sig"
            )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    @patch("accounts.services.payment_service.razorpay_client.fetch_payment")
    @patch("accounts.services.payment_service.razorpay_client.verify_checkout_signature")
    def test_valid_signature_and_matching_remote_marks_authorized(self, mock_verify, mock_fetch):
        mock_verify.return_value = True
        mock_fetch.return_value = {
            "order_id": self.payment.order_id, "amount": self.payment.amount,
            "currency": self.payment.currency, "method": "card",
        }
        payment = payment_service.verify_checkout_callback(
            user=self.user, order_id=self.payment.order_id, payment_id="pay_1", signature="sig"
        )
        self.assertEqual(payment.status, Payment.Status.AUTHORIZED)
        self.assertEqual(payment.payment_id, "pay_1")

    @patch("accounts.services.payment_service.razorpay_client.fetch_payment")
    @patch("accounts.services.payment_service.razorpay_client.verify_checkout_signature")
    def test_checkout_callback_never_activates_subscription(self, mock_verify, mock_fetch):
        """Step 18/28: browser-reported success alone must never grant Premium."""
        mock_verify.return_value = True
        mock_fetch.return_value = {
            "order_id": self.payment.order_id, "amount": self.payment.amount,
            "currency": self.payment.currency, "method": "card",
        }
        payment_service.verify_checkout_callback(
            user=self.user, order_id=self.payment.order_id, payment_id="pay_1", signature="sig"
        )
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.PENDING)
        self.assertFalse(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    @patch("accounts.services.payment_service.razorpay_client.verify_checkout_signature")
    def test_already_captured_payment_is_idempotent_noop(self, mock_verify):
        self.payment.status = Payment.Status.CAPTURED
        self.payment.save(update_fields=["status"])
        payment = payment_service.verify_checkout_callback(
            user=self.user, order_id=self.payment.order_id, payment_id="pay_1", signature="sig"
        )
        self.assertEqual(payment.status, Payment.Status.CAPTURED)
        mock_verify.assert_not_called()


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class WebhookPaymentCapturedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer3", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_abc"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

    def _process(self, payload, event_id="evt_1"):
        raw = json.dumps(payload).encode()
        return payment_service.process_webhook_event(
            event_id=event_id, event_type=payload["event"], raw_body=raw, payload=payload
        )

    def test_captured_activates_pending_subscription(self):
        payload = _captured_payload(order_id="order_abc", payment_id="pay_1", amount=self.payment.amount)
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.PROCESSED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CAPTURED)
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.ACTIVE)
        self.assertTrue(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    def test_captured_with_wrong_amount_does_not_activate(self):
        """Step 17: server-side amount verification before activation."""
        payload = _captured_payload(order_id="order_abc", payment_id="pay_1", amount=1)
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.PROCESSED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.PENDING)
        self.assertFalse(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    def test_captured_with_wrong_currency_does_not_activate(self):
        payload = _captured_payload(
            order_id="order_abc", payment_id="pay_1", amount=self.payment.amount, currency="USD"
        )
        self._process(payload)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)

    def test_captured_for_unknown_order_id_raises_not_found_recorded_as_failed(self):
        payload = _captured_payload(order_id="order_does_not_exist", payment_id="pay_1", amount=1000)
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.FAILED)

    def test_duplicate_webhook_delivery_is_idempotent(self):
        payload = _captured_payload(order_id="order_abc", payment_id="pay_1", amount=self.payment.amount)
        self._process(payload, event_id="evt_dup")
        self._process(payload, event_id="evt_dup")  # exact same event_id redelivered

        self.assertEqual(ProviderEvent.objects.filter(event_id="evt_dup").count(), 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CAPTURED)
        # Subscription must not have been renewed/re-activated twice.
        self.assertEqual(
            self.payment.subscription.audit_logs.filter(event_type="ACTIVATED").count(), 1
        )

    def test_second_distinct_captured_event_for_already_captured_payment_is_noop(self):
        payload = _captured_payload(order_id="order_abc", payment_id="pay_1", amount=self.payment.amount)
        self._process(payload, event_id="evt_1")
        # A different event_id (e.g. a genuinely separate delivery from
        # Razorpay for the same underlying payment) must still be a no-op
        # at the Payment level, not double-activate.
        self._process(payload, event_id="evt_2_same_payment")
        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.subscription.audit_logs.filter(event_type="ACTIVATED").count(), 1
        )

    def test_renewal_captured_event_renews_active_subscription(self):
        # First payment captures and activates.
        self._process(_captured_payload(order_id="order_abc", payment_id="pay_1", amount=self.payment.amount))
        self.payment.subscription.refresh_from_db()
        old_period_end = self.payment.subscription.current_period_end

        # Second payment for a renewal, same subscription.
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_renewal"}
            payment2, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )
        self._process(
            _captured_payload(order_id="order_renewal", payment_id="pay_2", amount=payment2.amount),
            event_id="evt_renewal",
        )
        self.payment.subscription.refresh_from_db()
        self.assertGreater(self.payment.subscription.current_period_end, old_period_end)

    def test_unhandled_event_type_recorded_as_ignored(self):
        payload = {"event": "refund.created", "payload": {}}
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.IGNORED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class WebhookPaymentFailedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer4", password="x")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_xyz"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

    def _process(self, payload, event_id="evt_f1"):
        raw = json.dumps(payload).encode()
        return payment_service.process_webhook_event(
            event_id=event_id, event_type=payload["event"], raw_body=raw, payload=payload
        )

    def test_failed_first_payment_does_not_grant_premium(self):
        payload = _failed_payload(order_id="order_xyz", payment_id="pay_1")
        self._process(payload)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertFalse(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    def test_failed_first_payment_cancels_pending_subscription(self):
        payload = _failed_payload(order_id="order_xyz", payment_id="pay_1")
        self._process(payload)
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.CANCELLED)

    def test_user_can_retry_after_failed_first_payment(self):
        self._process(_failed_payload(order_id="order_xyz", payment_id="pay_1"))
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_retry"}
            payment2, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )
        self.assertNotEqual(payment2.subscription_id, self.payment.subscription_id)

    def test_failed_renewal_marks_active_subscription_past_due(self):
        self._process(_captured_payload(order_id="order_xyz", payment_id="pay_1", amount=self.payment.amount))
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.ACTIVE)

        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_renewal_fail"}
            payment2, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )
        self._process(
            _failed_payload(order_id="order_renewal_fail", payment_id="pay_2"), event_id="evt_renewal_fail"
        )
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.PAST_DUE)
        # Still entitled during PAST_DUE -- grace handling, not immediate revoke.
        self.assertTrue(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    def test_duplicate_failed_webhook_is_idempotent(self):
        payload = _failed_payload(order_id="order_xyz", payment_id="pay_1")
        self._process(payload, event_id="evt_dup_fail")
        self._process(payload, event_id="evt_dup_fail")
        self.assertEqual(ProviderEvent.objects.filter(event_id="evt_dup_fail").count(), 1)


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class WebhookRefundProcessedTests(TestCase):
    """
    PHASE 8B — closes the gap documented in PHASE_4_COMPLETION_AUDIT.md:
    "Refunds are modeled but not processed."
    """

    def setUp(self):
        self.user = User.objects.create_user(username="buyer5", password="x")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_r1"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )
        # Capture it first -- a refund only ever follows a real capture.
        self._process(
            _captured_payload(order_id="order_r1", payment_id="pay_r1", amount=self.payment.amount),
            event_id="evt_capture_r1",
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CAPTURED)

    def _process(self, payload, event_id="evt_refund_1"):
        raw = json.dumps(payload).encode()
        return payment_service.process_webhook_event(
            event_id=event_id, event_type=payload["event"], raw_body=raw, payload=payload
        )

    def test_full_refund_marks_payment_refunded_and_revokes_access(self):
        payload = _refund_payload(payment_id="pay_r1", amount=self.payment.amount)
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.PROCESSED)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)
        self.assertEqual(self.payment.metadata.get("refund_id"), "rfnd_1")

        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.EXPIRED)
        self.assertFalse(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    def test_partial_refund_does_not_change_status_or_revoke_access(self):
        payload = _refund_payload(payment_id="pay_r1", amount=self.payment.amount // 2)
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.PROCESSED)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CAPTURED)  # unchanged
        self.assertEqual(len(self.payment.metadata.get("partial_refunds", [])), 1)

        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.ACTIVE)
        self.assertTrue(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )

    def test_duplicate_refund_webhook_is_idempotent(self):
        payload = _refund_payload(payment_id="pay_r1", amount=self.payment.amount)
        self._process(payload, event_id="evt_dup_refund")
        self._process(payload, event_id="evt_dup_refund")
        self.assertEqual(ProviderEvent.objects.filter(event_id="evt_dup_refund").count(), 1)
        self.payment.subscription.refresh_from_db()
        # Only one EXPIRED audit-log entry -- not double-expired.
        self.assertEqual(
            self.payment.subscription.audit_logs.filter(event_type="EXPIRED").count(), 1
        )

    def test_second_distinct_refund_event_for_already_refunded_payment_is_noop(self):
        self._process(_refund_payload(payment_id="pay_r1", amount=self.payment.amount), event_id="evt_a")
        self._process(_refund_payload(payment_id="pay_r1", amount=self.payment.amount), event_id="evt_b")
        self.payment.subscription.refresh_from_db()
        self.assertEqual(
            self.payment.subscription.audit_logs.filter(event_type="EXPIRED").count(), 1
        )

    def test_refund_for_unknown_payment_id_recorded_as_failed(self):
        # Mirrors WebhookPaymentCapturedTests.
        # test_captured_for_unknown_order_id_raises_not_found_recorded_as_failed
        # -- an unresolvable webhook is operator-visible (FAILED), never
        # silently swallowed.
        payload = _refund_payload(payment_id="pay_does_not_exist", amount=1000)
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.FAILED)

    def test_refund_missing_payment_id_recorded_as_failed(self):
        payload = {"event": "refund.processed", "payload": {"refund": {"entity": {"amount": 1000}}}}
        event = self._process(payload)
        self.assertEqual(event.status, ProviderEvent.Status.FAILED)

    def test_refund_superseded_by_later_renewal_does_not_revoke_current_access(self):
        # A second, later payment renews the same subscription...
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_renewal_r1"}
            payment2, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )
        self._process(
            _captured_payload(order_id="order_renewal_r1", payment_id="pay_r2", amount=payment2.amount),
            event_id="evt_capture_r2",
        )
        # ...then the OLD (already-superseded) payment gets refunded.
        self._process(_refund_payload(payment_id="pay_r1", amount=self.payment.amount), event_id="evt_old_refund")

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)  # payment record itself still updates
        self.payment.subscription.refresh_from_db()
        # But current access, backed by the newer payment, is untouched.
        self.assertEqual(self.payment.subscription.status, Subscription.Status.ACTIVE)
        self.assertTrue(
            UserEntitlement.objects.filter(user=self.user, plan__code="PREMIUM", status="ACTIVE").exists()
        )
