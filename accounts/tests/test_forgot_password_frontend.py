"""
PHASE 3B STEP 4 — forget.html frontend-contract tests.

accounts/tests/test_forgot_password.py already covers the backend
contract thoroughly (enumeration resistance, full flow, wrong/expired
OTP, max attempts, cooldown/window, weak password, session-cleared).
This file only adds the frontend-facing checks the Phase 3B instructions
called out that weren't already covered there: the page actually
renders on the new template, a successful reset does NOT leave the user
authenticated, and replaying the reset-password call after a completed
reset fails cleanly instead of silently succeeding twice.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, Client, override_settings

from accounts.models import EmailOTP


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class ForgetPageFrontendTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="frontend@example.com",
            email="frontend@example.com",
            password="Original-Passphrase1!",
        )

    def test_forgot_page_renders_on_rebuilt_template(self):
        response = self.client.get("/forgot/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Real endpoint URLs present (not a blank/dead form action), and
        # the real 6-box OTP UI is present.
        self.assertIn('action="/forgot/send-otp/"', content)
        self.assertIn("/forgot/resend-otp/", content)
        self.assertIn("/forgot/verify-otp/", content)
        self.assertIn("/forgot/reset-password/", content)
        self.assertEqual(content.count("data-index="), 6)
        # No leftover independent design system / dead alert() calls.
        self.assertNotIn("alert(", content)

    def test_user_not_authenticated_after_successful_reset(self):
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "frontend@example.com"})
        otp = EmailOTP.objects.get(email="frontend@example.com", purpose="reset").otp
        self.client.post("/forgot/verify-otp/", {"otp": otp})
        with patch("accounts.views.send_password_changed_email"):
            response = self.client.post(
                "/forgot/reset-password/", {"password": "Brand-New-Passphrase99!"}
            )

        self.assertEqual(response.json()["status"], "password_reset")
        # The session was flushed server-side -- there must be no
        # authenticated user riding along with this response. The
        # frontend must not (and per its own JS, does not) assume login.
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_replaying_reset_after_completion_fails_cleanly_not_silently(self):
        """A duplicate/replayed submit of the reset-password call, after
        the flow already completed once, must not succeed a second time
        or corrupt state -- it should fail with the same 'not verified'
        guard, since the session was already flushed."""
        with patch("accounts.views.send_reset_otp"):
            self.client.post("/forgot/send-otp/", {"email": "frontend@example.com"})
        otp = EmailOTP.objects.get(email="frontend@example.com", purpose="reset").otp
        self.client.post("/forgot/verify-otp/", {"otp": otp})
        with patch("accounts.views.send_password_changed_email"):
            first = self.client.post(
                "/forgot/reset-password/", {"password": "Brand-New-Passphrase99!"}
            )
        self.assertEqual(first.json()["status"], "password_reset")

        # Replay with a different password, simulating a duplicate submit
        # (e.g. a double-click that fired two requests) arriving after
        # the session was already flushed.
        second = self.client.post(
            "/forgot/reset-password/", {"password": "Another-Passphrase-2!"}
        )
        self.assertEqual(second.status_code, 403)
        self.assertEqual(second.json()["error"], "OTP not verified")

        # The password from the FIRST call is the one that actually took
        # effect; the replay must not have changed it again.
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-New-Passphrase99!"))
