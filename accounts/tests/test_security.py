import logging
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, Client, override_settings

from accounts.email.providers.brevo_provider import BrevoProvider
from accounts.models import EmailOTP
from accounts.tests.helpers import FakeResponse


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class OtpAndSecretsNotLeakedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    @patch("accounts.views.send_registration_otp")
    def test_otp_not_present_in_signup_json_or_session(self, mock_send):
        self.client.post(
            "/signup/",
            {
                "name": "Ada",
                "email": "ada@example.com",
                "password": "a-very-uncommon-Passphrase42!",
                "confirmPassword": "a-very-uncommon-Passphrase42!",
            },
        )
        record = EmailOTP.objects.get(email="ada@example.com", purpose="signup")
        session = self.client.session
        for value in session.values():
            self.assertNotIn(record.otp, str(value))

    def test_otp_verify_error_response_never_echoes_submitted_or_real_otp(self):
        with patch("accounts.views.send_registration_otp"):
            self.client.post(
                "/signup/",
                {
                    "name": "Ada",
                    "email": "ada@example.com",
                    "password": "a-very-uncommon-Passphrase42!",
                    "confirmPassword": "a-very-uncommon-Passphrase42!",
                },
            )
        real_otp = EmailOTP.objects.get(email="ada@example.com", purpose="signup").otp
        response = self.client.post("/verify-otp/", {"otp": "000000"})
        self.assertNotIn(real_otp, response.content.decode())

    def test_provider_log_records_never_contain_api_key(self):
        logger_output = StringIO()
        handler = logging.StreamHandler(logger_output)
        logger = logging.getLogger("accounts.email.providers.brevo_provider")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            provider = BrevoProvider(
                api_key="super-secret-key-value",
                from_email="noreply@example.com",
                from_name="Test",
                timeout_seconds=5.0,
            )
            with patch("accounts.email.providers.brevo_provider.requests.post") as mock_post:
                mock_post.return_value = FakeResponse(401, json_body={"code": "unauthorized"})
                try:
                    provider.send("user@example.com", "Subject", "<p>Body</p>", "req-1")
                except Exception:
                    pass
        finally:
            logger.removeHandler(handler)

        self.assertNotIn("super-secret-key-value", logger_output.getvalue())

    def test_profile_password_change_endpoint_never_returns_password(self):
        user = User.objects.create_user(
            username="known@example.com", email="known@example.com", password="Original-Passphrase1!"
        )
        self.client.force_login(user)
        response = self.client.post(
            "/profile/change-password/",
            data='{"currentPassword": "Original-Passphrase1!", "newPassword": "Second-Passphrase2!"}',
            content_type="application/json",
        )
        self.assertNotIn("Second-Passphrase2!", response.content.decode())
        self.assertNotIn("Original-Passphrase1!", response.content.decode())


class CsrfProtectionTests(TestCase):
    def setUp(self):
        # enforce_csrf_checks=True — the default test Client disables CSRF.
        self.client = Client(enforce_csrf_checks=True)

    def test_signup_post_without_csrf_token_is_rejected(self):
        response = self.client.post(
            "/signup/",
            {
                "name": "Ada",
                "email": "ada@example.com",
                "password": "a-very-uncommon-Passphrase42!",
                "confirmPassword": "a-very-uncommon-Passphrase42!",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_login_post_without_csrf_token_is_rejected(self):
        response = self.client.post("/login/", {"email": "a@example.com", "password": "x"})
        self.assertEqual(response.status_code, 403)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    @patch("accounts.views.send_registration_otp")
    def test_ip_throttle_caps_signup_attempts(self, mock_send):
        from accounts.rate_limit import IP_MAX_PER_WINDOW

        last_response = None
        for i in range(IP_MAX_PER_WINDOW + 3):
            last_response = self.client.post(
                "/signup/",
                {
                    "name": "Ada",
                    "email": f"ada{i}@example.com",
                    "password": "a-very-uncommon-Passphrase42!",
                    "confirmPassword": "a-very-uncommon-Passphrase42!",
                },
            )
        # Once the IP cap is exceeded, the response is the throttled
        # error page, not a redirect to verify-otp.
        self.assertEqual(last_response.status_code, 200)
        self.assertContains(last_response, "Too many")

    def test_forgot_password_brute_force_locked_after_max_attempts(self):
        User.objects.create_user(
            username="known@example.com", email="known@example.com", password="Original-Passphrase1!"
        )
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "known@example.com"})

        statuses = []
        for _ in range(7):
            response = self.client.post("/forgot/verify-otp/", {"otp": "999999"})
            statuses.append(response.status_code)

        self.assertIn(403, statuses)
