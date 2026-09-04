from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, Client, override_settings

from accounts.email.exceptions import ProviderUnavailableError
from accounts.models import EmailOTP


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class ForgotPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="known@example.com", email="known@example.com", password="Original-Passphrase1!"
        )

    @patch("accounts.views.send_reset_otp")
    def test_registered_email_gets_generic_response_and_real_otp(self, mock_send):
        response = self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "otp_sent")
        self.assertTrue(EmailOTP.objects.filter(email="known@example.com", purpose="reset").exists())
        mock_send.assert_called_once()

    @patch("accounts.views.send_reset_otp")
    def test_unregistered_email_gets_identical_generic_response_no_otp(self, mock_send):
        response = self.client.post("/forgot/send-otp/", {"email": "nobody@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "otp_sent")
        self.assertFalse(EmailOTP.objects.filter(email="nobody@example.com").exists())
        mock_send.assert_not_called()

    @patch("accounts.views.send_reset_otp")
    def test_enumeration_resistant_response_bodies_match(self, mock_send):
        registered = self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        cache.clear()
        unregistered = Client().post("/forgot/send-otp/", {"email": "nobody@example.com"})
        self.assertEqual(registered.json(), unregistered.json())
        self.assertEqual(registered.status_code, unregistered.status_code)

    def test_missing_email_returns_400(self):
        response = self.client.post("/forgot/send-otp/", {})
        self.assertEqual(response.status_code, 400)

    @patch("accounts.views.send_reset_otp")
    def test_otp_send_verify_reset_full_flow(self, mock_send):
        self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp

        verify = self.client.post("/forgot/verify-otp/", {"otp": otp})
        self.assertEqual(verify.json()["status"], "verified")

        reset = self.client.post(
            "/forgot/reset-password/", {"password": "Brand-New-Passphrase99!"}
        )
        self.assertEqual(reset.json()["status"], "password_reset")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-New-Passphrase99!"))

    def test_wrong_otp_rejected(self):
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        response = self.client.post("/forgot/verify-otp/", {"otp": "000000"})
        self.assertEqual(response.status_code, 400)

    def test_max_attempts_enforced(self):
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        for _ in range(6):
            response = self.client.post("/forgot/verify-otp/", {"otp": "000000"})
        self.assertIn(response.status_code, (400, 403))

    def test_reset_without_verification_forbidden(self):
        response = self.client.post("/forgot/reset-password/", {"password": "Whatever-Passphrase1!"})
        self.assertEqual(response.status_code, 403)

    def test_weak_new_password_rejected(self):
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp
        self.client.post("/forgot/verify-otp/", {"otp": otp})

        response = self.client.post("/forgot/reset-password/", {"password": "123"})
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("123"))

    @patch("accounts.views.send_reset_otp")
    def test_send_otp_respects_cooldown(self, mock_send):
        self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        first_otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp

        # Second immediate request should be throttled and NOT rotate the OTP.
        self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        second_otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp
        self.assertEqual(first_otp, second_otp)
        self.assertEqual(mock_send.call_count, 1)

    @patch("accounts.views.send_reset_otp")
    def test_resend_rotates_otp_after_cooldown_cleared(self, mock_send):
        self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        first_otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp
        cache.clear()
        self.client.post("/forgot/resend-otp/")
        second_otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp
        self.assertNotEqual(first_otp, second_otp)

    @patch("accounts.views.send_reset_otp", side_effect=ProviderUnavailableError("down"))
    def test_brevo_failure_on_send_does_not_leak_via_enumeration_channel(self, mock_send):
        """Even if Brevo fails, the response must stay enumeration-generic —
        it must not accidentally differ from the unregistered-email case."""
        response = self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        self.assertEqual(response.json()["status"], "otp_sent")

    def test_session_cleared_after_successful_reset(self):
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        otp = EmailOTP.objects.get(email="known@example.com", purpose="reset").otp
        self.client.post("/forgot/verify-otp/", {"otp": otp})
        self.client.post("/forgot/reset-password/", {"password": "Brand-New-Passphrase99!"})

        session = self.client.session
        self.assertNotIn("otp_verified", session)
        self.assertNotIn("reset_email", session)

    # PHASE 8A SECURITY HARDENING -- closes a verify-step enumeration
    # side channel: forgot_send_otp() already returns an identical
    # generic response for a registered vs. unregistered email (see
    # test_enumeration_resistant_response_bodies_match above), but no
    # EmailOTP row was ever created for an unregistered email, so
    # otp_service.verify_otp()'s EmailOTP.DoesNotExist branch used to
    # return a message ("Verification code not found or already used...")
    # distinguishable from the wrong-code-on-a-real-OTP branch ("Invalid
    # verification code."). An attacker could send-otp for a candidate
    # email, immediately verify-otp with any guess, and use which
    # message came back to learn whether the account exists -- without
    # ever needing the real code. The two branches now return an
    # identical message and status.
    def test_verify_step_gives_identical_response_for_unregistered_email_and_wrong_code(self):
        # Unregistered email: send-otp (no OTP ever created), then guess.
        unreg_client = Client()
        with patch("accounts.views.send_reset_otp"):
            unreg_client.post("/forgot/send-otp/", {"email": "ghost@example.com"})
        self.assertFalse(EmailOTP.objects.filter(email="ghost@example.com").exists())
        unreg_response = unreg_client.post("/forgot/verify-otp/", {"otp": "000000"})

        # Registered email with a real, pending OTP: guess wrong.
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "known@example.com"})
        self.assertTrue(EmailOTP.objects.filter(email="known@example.com").exists())
        reg_wrong_response = self.client.post("/forgot/verify-otp/", {"otp": "000000"})

        self.assertEqual(unreg_response.status_code, reg_wrong_response.status_code)
        self.assertEqual(unreg_response.json(), reg_wrong_response.json())
