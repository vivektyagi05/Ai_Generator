"""
PHASE 4 Step 24/27 — accounts/webhook_views.py tests: signature required
and verified, CSRF-exempt (documented, not accidental), malformed/missing
payloads rejected safely, duplicate deliveries handled idempotently,
unknown event types don't error.
"""

import hashlib
import hmac
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from accounts.models import Payment, ProviderEvent, Subscription
from unittest.mock import patch

from accounts.services import payment_service


WEBHOOK_URL = "/api/webhooks/razorpay/"


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class RazorpayWebhookViewTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(username="webhookbuyer", password="x")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_wh1"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

    def _captured_body(self, order_id=None, amount=None):
        order_id = order_id or self.payment.order_id
        amount = amount if amount is not None else self.payment.amount
        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_wh1", "order_id": order_id,
                        "amount": amount, "currency": "INR",
                        "status": "captured", "method": "upi",
                    }
                }
            },
        }
        return json.dumps(payload).encode()

    def test_no_csrf_token_required_endpoint_still_reachable(self):
        """Step 13: CSRF exemption is deliberate -- a request with no CSRF
        token/cookie at all must still reach signature verification, not
        be blocked by Django's CSRF middleware first."""
        body = self._captured_body()
        sig = _sign(body, "whsecret")
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_csrf_test",
        )
        self.assertEqual(response.status_code, 200)

    def test_missing_signature_header_rejected(self):
        body = self._captured_body()
        response = self.client.post(WEBHOOK_URL, data=body, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_signature_rejected(self):
        body = self._captured_body()
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE="0" * 64, HTTP_X_RAZORPAY_EVENT_ID="evt_bad_sig",
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    def test_tampered_body_after_signing_rejected(self):
        """Signature computed over the original body must not verify
        against a body that was subsequently modified in transit."""
        original_body = self._captured_body(amount=self.payment.amount)
        sig = _sign(original_body, "whsecret")
        tampered_body = self._captured_body(amount=1)  # attacker lowers the amount
        response = self.client.post(
            WEBHOOK_URL, data=tampered_body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_tampered",
        )
        self.assertEqual(response.status_code, 400)

    def test_valid_signature_captures_and_activates(self):
        body = self._captured_body()
        sig = _sign(body, "whsecret")
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_valid",
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CAPTURED)
        self.payment.subscription.refresh_from_db()
        self.assertEqual(self.payment.subscription.status, Subscription.Status.ACTIVE)

    def test_duplicate_delivery_same_event_id_processed_once(self):
        body = self._captured_body()
        sig = _sign(body, "whsecret")
        for _ in range(2):
            response = self.client.post(
                WEBHOOK_URL, data=body, content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_replay",
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(ProviderEvent.objects.filter(event_id="evt_replay").count(), 1)
        self.assertEqual(
            self.payment.subscription.audit_logs.filter(event_type="ACTIVATED").count(), 1
        )

    def test_malformed_json_body_with_valid_signature_rejected(self):
        body = b"not valid json{{{"
        sig = _sign(body, "whsecret")
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_malformed",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_event_type_does_not_error(self):
        payload = {"event": "refund.created", "payload": {}}
        body = json.dumps(payload).encode()
        sig = _sign(body, "whsecret")
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_unknown",
        )
        self.assertEqual(response.status_code, 200)
        event = ProviderEvent.objects.get(event_id="evt_unknown")
        self.assertEqual(event.status, ProviderEvent.Status.IGNORED)

    def test_get_method_not_allowed(self):
        response = self.client.get(WEBHOOK_URL)
        self.assertEqual(response.status_code, 405)

    def test_missing_event_id_header_still_processed_but_not_silently_trusted(self):
        """No X-Razorpay-Event-Id -- degraded idempotency, but a
        signature-valid, well-formed event must still be processed rather
        than dropped (Step 13/15: never silently drop a legitimate
        payment notification)."""
        body = self._captured_body()
        sig = _sign(body, "whsecret")
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CAPTURED)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="")
    def test_missing_webhook_secret_fails_safely(self):
        body = self._captured_body()
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE="anything", HTTP_X_RAZORPAY_EVENT_ID="evt_no_secret",
        )
        self.assertEqual(response.status_code, 503)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    def test_failed_payment_webhook_does_not_activate(self):
        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {"id": "pay_fail1", "order_id": self.payment.order_id, "error_description": "insufficient funds"}
                }
            },
        }
        body = json.dumps(payload).encode()
        sig = _sign(body, "whsecret")
        response = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID="evt_failed",
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
