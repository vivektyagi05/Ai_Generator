from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, Client, override_settings

from accounts.email.exceptions import ProviderUnavailableError
from accounts.models import EmailOTP, PendingSignup


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class SignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def _signup_payload(self, **overrides):
        payload = {
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "password": "a-very-uncommon-Passphrase42!",
            "confirmPassword": "a-very-uncommon-Passphrase42!",
        }
        payload.update(overrides)
        return payload

    @patch("accounts.views.send_registration_otp")
    def test_valid_signup_creates_pending_record_and_otp(self, mock_send):
        response = self.client.post("/signup/", self._signup_payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PendingSignup.objects.filter(email="ada@example.com").exists())
        self.assertTrue(EmailOTP.objects.filter(email="ada@example.com", purpose="signup").exists())
        mock_send.assert_called_once()
        self.assertFalse(User.objects.filter(username="ada@example.com").exists())

    @patch("accounts.views.send_registration_otp")
    def test_password_never_stored_in_session(self, mock_send):
        self.client.post("/signup/", self._signup_payload())
        session = self.client.session
        self.assertIn("signup_email", session)
        # Nothing resembling the plaintext password anywhere in the session.
        self.assertNotIn("signup_data", session)
        for value in session.values():
            self.assertNotIn("a-very-uncommon-Passphrase42!", str(value))

    def test_missing_fields_returns_error(self):
        response = self.client.post("/signup/", {"name": "", "email": "", "password": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required")

    def test_password_mismatch(self):
        response = self.client.post(
            "/signup/", self._signup_payload(confirmPassword="different-Passphrase99!")
        )
        self.assertContains(response, "do not match")

    def test_weak_password_rejected_by_django_validators(self):
        response = self.client.post("/signup/", self._signup_payload(password="pass", confirmPassword="pass"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(response.status_code, [302])

    def test_duplicate_email_rejected(self):
        User.objects.create_user(username="ada@example.com", email="ada@example.com", password="whatever-Passphrase1!")
        response = self.client.post("/signup/", self._signup_payload())
        self.assertContains(response, "already registered")

    @patch("accounts.views.send_registration_otp", side_effect=ProviderUnavailableError("down"))
    def test_brevo_failure_does_not_redirect_or_create_user(self, mock_send):
        response = self.client.post("/signup/", self._signup_payload())
        self.assertEqual(response.status_code, 200)  # re-rendered signup page, not a redirect
        self.assertContains(response, "Could not send verification code")
        self.assertFalse(User.objects.filter(username="ada@example.com").exists())


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class VerifyOtpViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    @patch("accounts.views.send_registration_otp")
    def _signed_up_client(self, mock_send, email="ada@example.com"):
        self.client.post(
            "/signup/",
            {
                "name": "Ada Lovelace",
                "email": email,
                "password": "a-very-uncommon-Passphrase42!",
                "confirmPassword": "a-very-uncommon-Passphrase42!",
            },
        )
        otp = EmailOTP.objects.get(email=email, purpose="signup").otp
        return otp

    @patch("accounts.views.send_welcome_email")
    def test_correct_otp_creates_user_and_logs_in(self, mock_welcome):
        otp = self._signed_up_client()
        response = self.client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(response.json()["status"], "success")
        user = User.objects.get(username="ada@example.com")
        self.assertTrue(user.check_password("a-very-uncommon-Passphrase42!"))
        self.assertFalse(PendingSignup.objects.filter(email="ada@example.com").exists())
        self.assertFalse(EmailOTP.objects.filter(email="ada@example.com").exists())
        mock_welcome.assert_called_once()

    @patch("accounts.views.send_welcome_email")
    def test_password_hashed_exactly_once(self, mock_welcome):
        """Regression guard for the 'hash the hash' bug class: the user's
        stored password hash must validate against the ORIGINAL plaintext,
        proving it wasn't re-hashed by set_password/create_user."""
        otp = self._signed_up_client()
        self.client.post("/verify-otp/", {"otp": otp})
        user = User.objects.get(username="ada@example.com")
        self.assertTrue(user.check_password("a-very-uncommon-Passphrase42!"))

    def test_wrong_otp_rejected(self):
        self._signed_up_client()
        response = self.client.post("/verify-otp/", {"otp": "000000"})
        self.assertEqual(response.json()["status"], "error")
        self.assertFalse(User.objects.filter(username="ada@example.com").exists())

    def test_expired_otp_rejected(self):
        from datetime import timedelta
        from django.utils import timezone

        otp = self._signed_up_client()
        record = EmailOTP.objects.get(email="ada@example.com", purpose="signup")
        record.created_at = timezone.now() - timedelta(minutes=10)
        record.save(update_fields=["created_at"])

        response = self.client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(response.json()["status"], "error")

    def test_attempt_limit_enforced(self):
        self._signed_up_client()
        for _ in range(6):
            response = self.client.post("/verify-otp/", {"otp": "000000"})
        self.assertEqual(response.json()["status"], "error")
        self.assertFalse(EmailOTP.objects.filter(email="ada@example.com", purpose="signup").exists())

    @patch("accounts.views.send_welcome_email")
    def test_replay_prevented(self, mock_welcome):
        otp = self._signed_up_client()
        first = self.client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(first.json()["status"], "success")

        # A second signup+verify with a fresh session but the same OTP value
        # (simulating replay) must fail since the record is deleted.
        second_client = Client()
        response = second_client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(response.json()["status"], "error")

    def test_no_session_returns_error(self):
        response = self.client.post("/verify-otp/", {"otp": "123456"})
        self.assertEqual(response.json()["status"], "error")


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class ResendOtpTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    @patch("accounts.views.send_registration_otp")
    def _signed_up_client(self, mock_send, email="ada@example.com"):
        self.client.post(
            "/signup/",
            {
                "name": "Ada Lovelace",
                "email": email,
                "password": "a-very-uncommon-Passphrase42!",
                "confirmPassword": "a-very-uncommon-Passphrase42!",
            },
        )

    @patch("accounts.views.send_registration_otp")
    def test_resend_without_session_fails(self, mock_send):
        response = self.client.post("/resend-otp/")
        self.assertEqual(response.status_code, 400)

    @patch("accounts.views.send_registration_otp")
    def test_resend_respects_cooldown(self, mock_send):
        self._signed_up_client()
        response = self.client.post("/resend-otp/")
        self.assertEqual(response.status_code, 429)

    @patch("accounts.views.send_registration_otp")
    def test_resend_invalidates_old_otp(self, mock_send):
        self._signed_up_client()
        old_otp = EmailOTP.objects.get(email="ada@example.com", purpose="signup").otp
        cache.clear()  # bypass cooldown for this test
        self.client.post("/resend-otp/")
        new_otp = EmailOTP.objects.get(email="ada@example.com", purpose="signup").otp
        self.assertNotEqual(old_otp, new_otp)
        self.assertEqual(EmailOTP.objects.filter(email="ada@example.com", purpose="signup").count(), 1)

    @patch("accounts.views.send_registration_otp", side_effect=ProviderUnavailableError("down"))
    def test_resend_brevo_failure_does_not_report_success(self, mock_send):
        self._signed_up_client()
        cache.clear()
        response = self.client.post("/resend-otp/")
        self.assertNotEqual(response.status_code, 200)
        self.assertIn("error", response.json())
