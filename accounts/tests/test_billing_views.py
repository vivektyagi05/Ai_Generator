"""
PHASE 4 Step 22/23/24 — billing API tests: authentication required, no
client-controlled amount/status, no cross-user access (IDOR), no
forbidden endpoints exist.
"""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from accounts import billing_config
from accounts.models import Payment, Plan, Subscription
from accounts.services import payment_service


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class CreateOrderViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="webbuyer", password="x")

    def test_anonymous_cannot_create_order(self):
        response = self.client.post(
            "/api/billing/create-order/", data=json.dumps({"plan": "PREMIUM", "billing_interval": "MONTHLY"}),
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/billing/create-order/")
        self.assertEqual(response.status_code, 405)

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_authenticated_user_can_create_order(self, mock_create_order):
        mock_create_order.return_value = {"id": "order_web1"}
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/create-order/", data=json.dumps({"plan": "PREMIUM", "billing_interval": "MONTHLY"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["order_id"], "order_web1")
        self.assertIn("key_id", data)
        self.assertEqual(data["key_id"], "rzp_test_key")

    def test_response_never_contains_key_secret(self):
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_create_order:
            mock_create_order.return_value = {"id": "order_web2"}
            self.client.force_login(self.user)
            response = self.client.post(
                "/api/billing/create-order/", data=json.dumps({"plan": "PREMIUM", "billing_interval": "MONTHLY"}),
                content_type="application/json",
            )
        self.assertNotIn("secret", response.content.decode().lower())

    @patch("accounts.services.payment_service.razorpay_client.create_order")
    def test_client_supplied_amount_is_ignored(self, mock_create_order):
        """Step 8/17: even if a client tries to smuggle an amount field in
        the body, the server never reads it -- amount is always resolved
        from billing_config."""
        mock_create_order.return_value = {"id": "order_web3"}
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/create-order/",
            data=json.dumps({"plan": "PREMIUM", "billing_interval": "MONTHLY", "amount": 1}),
            content_type="application/json",
        )
        data = response.json()
        expected_amount, _ = billing_config.resolve_price("PREMIUM", "MONTHLY")
        self.assertEqual(data["amount"], expected_amount)
        called_kwargs = mock_create_order.call_args
        self.assertEqual(called_kwargs.kwargs["amount"] if called_kwargs.kwargs else called_kwargs[1]["amount"], expected_amount)

    def test_free_plan_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/create-order/", data=json.dumps({"plan": "FREE", "billing_interval": "MONTHLY"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_json_body_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/create-order/", data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="", RAZORPAY_WEBHOOK_SECRET="")
    def test_missing_razorpay_config_fails_safely_503(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/create-order/", data=json.dumps({"plan": "PREMIUM", "billing_interval": "MONTHLY"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="", RAZORPAY_WEBHOOK_SECRET="")
    def test_missing_razorpay_config_returns_structured_code_no_secrets(self):
        # Phase C/J: the frontend needs a stable machine-readable code to
        # distinguish "not configured" (don't retry) from a transient
        # failure (do retry) -- and the response must never leak which
        # env var is missing, a traceback, or any secret.
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/create-order/", data=json.dumps({"plan": "PREMIUM", "billing_interval": "MONTHLY"}),
            content_type="application/json",
        )
        body = response.json()
        self.assertEqual(body.get("code"), "BILLING_NOT_CONFIGURED")
        self.assertNotIn("RAZORPAY_KEY_ID", body["error"])
        self.assertNotIn("RAZORPAY_KEY_SECRET", body["error"])
        self.assertNotIn("Traceback", body["error"])

    def test_no_activate_premium_endpoint_exists(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/billing/activate-premium/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_no_set_status_endpoint_exists(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/billing/set-status/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_no_set_amount_endpoint_exists(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/billing/set-amount/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 404)


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class VerifyPaymentViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="webbuyer2", password="x")
        self.other = User.objects.create_user(username="mallory3", password="x")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_vw1"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

    def test_anonymous_cannot_verify(self):
        response = self.client.post(
            "/api/billing/verify-payment/",
            data=json.dumps({"order_id": "order_vw1", "payment_id": "p1", "signature": "s1"}),
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 200)

    def test_missing_fields_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/verify-payment/", data=json.dumps({"order_id": "order_vw1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_other_users_order_id_returns_404_not_someone_elses_payment(self):
        """Step 23: IDOR -- User A cannot verify/see User B's payment."""
        self.client.force_login(self.other)
        response = self.client.post(
            "/api/billing/verify-payment/",
            data=json.dumps({"order_id": self.payment.order_id, "payment_id": "p1", "signature": "s1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_forged_signature_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/billing/verify-payment/",
            data=json.dumps(
                {"order_id": self.payment.order_id, "payment_id": "pay_forged", "signature": "0" * 64}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)


@override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret")
class PaymentStatusViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="webbuyer3", password="x")
        self.other = User.objects.create_user(username="mallory4", password="x")
        with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
            mock_order.return_value = {"id": "order_ps1"}
            self.payment, _ = payment_service.create_payment_order(
                user=self.user, plan_code="PREMIUM", billing_interval="MONTHLY"
            )

    def test_owner_can_read_own_payment_status(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/api/billing/payment-status/{self.payment.order_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CREATED")

    def test_other_user_cannot_read_payment_status(self):
        self.client.force_login(self.other)
        response = self.client.get(f"/api/billing/payment-status/{self.payment.order_id}/")
        self.assertEqual(response.status_code, 404)

    def test_anonymous_cannot_read_payment_status(self):
        response = self.client.get(f"/api/billing/payment-status/{self.payment.order_id}/")
        self.assertNotEqual(response.status_code, 200)

    def test_nonexistent_order_id_returns_same_404_as_someone_elses(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/billing/payment-status/order_totally_fake/")
        self.assertEqual(response.status_code, 404)
