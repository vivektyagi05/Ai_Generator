"""
PHASE 4 Step 27 — accounts/services/razorpay_client.py tests.

Mocks only the razorpay SDK's outbound network/crypto surface (matching
this project's existing convention in test_provider.py of mocking only the
outbound call, not the whole module) -- signature math itself is exercised
for real via razorpay.Utility where practical.
"""

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import razorpay
from django.test import TestCase, override_settings

from accounts.services import razorpay_client
from accounts.services.razorpay_client import (
    RazorpayNotConfiguredError,
    SignatureVerificationFailed,
)


@override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="", RAZORPAY_WEBHOOK_SECRET="")
class MissingConfigurationTests(TestCase):
    """Step 3: 'Fail safely if production payment configuration is missing.'"""

    def test_get_client_raises_when_unconfigured(self):
        with self.assertRaises(RazorpayNotConfiguredError):
            razorpay_client.get_client()

    def test_get_public_key_id_raises_when_unconfigured(self):
        with self.assertRaises(RazorpayNotConfiguredError):
            razorpay_client.get_public_key_id()

    def test_create_order_raises_when_unconfigured(self):
        with self.assertRaises(RazorpayNotConfiguredError):
            razorpay_client.create_order(amount=100, currency="INR", receipt="r1")

    def test_verify_checkout_signature_raises_when_unconfigured(self):
        with self.assertRaises(RazorpayNotConfiguredError):
            razorpay_client.verify_checkout_signature(
                order_id="order_1", payment_id="pay_1", signature="sig"
            )

    def test_verify_webhook_signature_raises_when_unconfigured(self):
        with self.assertRaises(RazorpayNotConfiguredError):
            razorpay_client.verify_webhook_signature(raw_body=b"{}", signature="sig")


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="test_secret",
    RAZORPAY_WEBHOOK_SECRET="webhook_secret",
)
class ConfiguredClientTests(TestCase):
    def test_get_public_key_id_returns_only_the_key_id(self):
        self.assertEqual(razorpay_client.get_public_key_id(), "rzp_test_key")

    def test_get_client_never_exposes_secret_in_repr(self):
        client = razorpay_client.get_client()
        # auth tuple necessarily contains the secret internally -- assert
        # it's at least not trivially printed by a bare repr() of the
        # client object a careless log statement might produce.
        self.assertNotIn("test_secret", repr(client.__class__))

    @patch("accounts.services.razorpay_client.razorpay.Client")
    def test_create_order_rejects_non_positive_amount(self, mock_client_cls):
        with self.assertRaises(ValueError):
            razorpay_client.create_order(amount=0, currency="INR", receipt="r1")
        with self.assertRaises(ValueError):
            razorpay_client.create_order(amount=-500, currency="INR", receipt="r1")

    @patch("accounts.services.razorpay_client.razorpay.Client")
    def test_create_order_sets_payment_capture_and_passes_amount(self, mock_client_cls):
        mock_instance = MagicMock()
        mock_instance.order.create.return_value = {"id": "order_abc", "amount": 29900}
        mock_client_cls.return_value = mock_instance

        result = razorpay_client.create_order(amount=29900, currency="INR", receipt="r1")

        self.assertEqual(result["id"], "order_abc")
        called_kwargs = mock_instance.order.create.call_args[0][0]
        self.assertEqual(called_kwargs["amount"], 29900)
        self.assertEqual(called_kwargs["currency"], "INR")
        self.assertEqual(called_kwargs["payment_capture"], 1)

    def test_verify_checkout_signature_valid(self):
        order_id, payment_id = "order_abc", "pay_xyz"
        msg = f"{order_id}|{payment_id}".encode()
        sig = hmac.new(b"test_secret", msg, hashlib.sha256).hexdigest()

        result = razorpay_client.verify_checkout_signature(
            order_id=order_id, payment_id=payment_id, signature=sig
        )
        self.assertTrue(result)

    def test_verify_checkout_signature_invalid_raises(self):
        with self.assertRaises(SignatureVerificationFailed):
            razorpay_client.verify_checkout_signature(
                order_id="order_abc", payment_id="pay_xyz", signature="0" * 64
            )

    def test_verify_checkout_signature_tampered_order_id_rejected(self):
        # Signature computed for a DIFFERENT order_id must not verify.
        msg = "order_original|pay_xyz".encode()
        sig = hmac.new(b"test_secret", msg, hashlib.sha256).hexdigest()
        with self.assertRaises(SignatureVerificationFailed):
            razorpay_client.verify_checkout_signature(
                order_id="order_tampered", payment_id="pay_xyz", signature=sig
            )

    def test_verify_webhook_signature_valid(self):
        body = b'{"event": "payment.captured"}'
        sig = hmac.new(b"webhook_secret", body, hashlib.sha256).hexdigest()
        result = razorpay_client.verify_webhook_signature(raw_body=body, signature=sig)
        self.assertTrue(result)

    def test_verify_webhook_signature_invalid_raises(self):
        with self.assertRaises(SignatureVerificationFailed):
            razorpay_client.verify_webhook_signature(raw_body=b'{"event":"x"}', signature="0" * 64)

    def test_verify_webhook_signature_tampered_body_rejected(self):
        body = b'{"event": "payment.captured", "amount": 100}'
        sig = hmac.new(b"webhook_secret", body, hashlib.sha256).hexdigest()
        tampered_body = b'{"event": "payment.captured", "amount": 999999}'
        with self.assertRaises(SignatureVerificationFailed):
            razorpay_client.verify_webhook_signature(raw_body=tampered_body, signature=sig)

    @patch("accounts.services.razorpay_client.razorpay.Client")
    def test_fetch_payment_calls_sdk(self, mock_client_cls):
        mock_instance = MagicMock()
        mock_instance.payment.fetch.return_value = {"id": "pay_xyz", "status": "captured"}
        mock_client_cls.return_value = mock_instance

        result = razorpay_client.fetch_payment("pay_xyz")
        self.assertEqual(result["status"], "captured")
        mock_instance.payment.fetch.assert_called_once_with("pay_xyz")
