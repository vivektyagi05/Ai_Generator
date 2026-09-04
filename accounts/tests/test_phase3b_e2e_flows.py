"""
PHASE 3B — FINAL INTEGRATION — end-to-end cross-page flow tests.

Exercises the full user journeys (Flows A-F from the completion-gate
instructions) through the real URLs/views via Django's test client. This
is request/response/session/DB-level verification, NOT browser
automation -- no JS execution, no real rendering/click simulation. See
PHASE_3B_AUTH_COMPLETION_AUDIT.md for what is and isn't covered by this
vs. genuine browser E2E (explicitly marked EXTERNAL VERIFICATION
REQUIRED there).
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, Client, override_settings

from accounts.models import EmailOTP, PendingSignup


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class FlowANewUserSignupToHome(TestCase):
    """FLOW A: public -> signup -> OTP -> verified -> home (authenticated)."""

    def setUp(self):
        cache.clear()
        self.client = Client()

    @patch("accounts.views.send_welcome_email")
    @patch("accounts.views.send_registration_otp")
    def test_full_signup_to_authenticated_home(self, mock_send_otp, mock_welcome):
        signup_resp = self.client.post(
            "/signup/",
            {
                "name": "Flow A User",
                "email": "flowa@example.com",
                "password": "Flow-A-Passphrase1!",
                "confirmPassword": "Flow-A-Passphrase1!",
            },
        )
        self.assertEqual(signup_resp.status_code, 302)
        self.assertEqual(signup_resp.url, "/verify-otp/")
        mock_send_otp.assert_called_once()
        self.assertFalse(User.objects.filter(username="flowa@example.com").exists())
        pending = PendingSignup.objects.get(email="flowa@example.com")
        self.assertNotEqual(pending.password_hash, "Flow-A-Passphrase1!")  # hashed, not plaintext

        otp_page = self.client.get("/verify-otp/")
        self.assertEqual(otp_page.status_code, 200)
        self.assertIn(b"flowa@example.com", otp_page.content)

        otp = EmailOTP.objects.get(email="flowa@example.com", purpose="signup").otp
        verify_resp = self.client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(verify_resp.json(), {"status": "success"})
        self.assertFalse(PendingSignup.objects.filter(email="flowa@example.com").exists())

        user = User.objects.get(username="flowa@example.com")
        self.assertEqual(user.first_name, "Flow A User")
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

        home_resp = self.client.get("/home/")
        self.assertEqual(home_resp.status_code, 200)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class FlowBExistingUserLogin(TestCase):
    """FLOW B: public -> login -> authenticated home, plus failure/next."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="flowb@example.com", email="flowb@example.com", password="Flow-B-Passphrase1!"
        )

    def test_valid_login_redirects_home_and_authenticates(self):
        resp = self.client.post("/login/", {"email": "flowb@example.com", "password": "Flow-B-Passphrase1!"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/home/")
        self.assertIn("_auth_user_id", self.client.session)

    def test_invalid_credentials_generic_error_no_enumeration(self):
        wrong_password = self.client.post("/login/", {"email": "flowb@example.com", "password": "wrong"})
        nonexistent = self.client.post("/login/", {"email": "nobody@example.com", "password": "wrong"})
        self.assertEqual(wrong_password.status_code, 200)
        self.assertEqual(nonexistent.status_code, 200)
        self.assertIn(b"Invalid email or password", wrong_password.content)
        self.assertIn(b"Invalid email or password", nonexistent.content)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_safe_next_redirect_honored(self):
        resp = self.client.post(
            "/login/",
            {"email": "flowb@example.com", "password": "Flow-B-Passphrase1!", "next": "/profile/"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/profile/")

    def test_unsafe_next_redirect_rejected(self):
        resp = self.client.post(
            "/login/",
            {
                "email": "flowb@example.com",
                "password": "Flow-B-Passphrase1!",
                "next": "https://evil.example.com/steal",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp.url)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class FlowCOtpNegativePaths(TestCase):
    """FLOW C negative paths: wrong OTP, expired signup session, resend cooldown."""

    def setUp(self):
        cache.clear()
        self.client = Client()

    @patch("accounts.views.send_registration_otp")
    def test_wrong_otp_then_correct_otp_still_succeeds(self, mock_send):
        self.client.post(
            "/signup/",
            {
                "name": "Flow C",
                "email": "flowc@example.com",
                "password": "Flow-C-Passphrase1!",
                "confirmPassword": "Flow-C-Passphrase1!",
            },
        )
        wrong = self.client.post("/verify-otp/", {"otp": "000000"})
        self.assertEqual(wrong.json()["status"], "error")
        self.assertIn("Invalid verification code", wrong.json()["message"])

        otp = EmailOTP.objects.get(email="flowc@example.com", purpose="signup").otp
        right = self.client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(right.json(), {"status": "success"})

    def test_verify_otp_without_signup_session_redirects_to_signup(self):
        resp = self.client.get("/verify-otp/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/signup/")

    @patch("accounts.views.send_registration_otp")
    def test_resend_before_cooldown_elapses_returns_429(self, mock_send):
        self.client.post(
            "/signup/",
            {
                "name": "Flow C2",
                "email": "flowc2@example.com",
                "password": "Flow-C2-Passphrase1!",
                "confirmPassword": "Flow-C2-Passphrase1!",
            },
        )
        resp = self.client.post("/resend-otp/")
        self.assertEqual(resp.status_code, 429)
        self.assertIn("wait a moment", resp.json()["error"])


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class FlowDForgotPasswordFullJourney(TestCase):
    """FLOW D: login page -> forgot -> OTP -> new password -> NOT auto-logged-in -> login works with new password."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="flowd@example.com", email="flowd@example.com", password="Old-Passphrase1!"
        )

    @patch("accounts.views.send_password_changed_email")
    @patch("accounts.views.send_reset_otp")
    def test_full_forgot_password_journey(self, mock_reset_otp, mock_changed_email):
        forgot_page = self.client.get("/forgot/")
        self.assertEqual(forgot_page.status_code, 200)

        send = self.client.post("/forgot/send-otp/", {"email": "flowd@example.com"})
        self.assertEqual(send.json()["status"], "otp_sent")

        otp = EmailOTP.objects.get(email="flowd@example.com", purpose="reset").otp
        verify = self.client.post("/forgot/verify-otp/", {"otp": otp})
        self.assertEqual(verify.json(), {"status": "verified"})

        reset = self.client.post("/forgot/reset-password/", {"password": "New-Passphrase-99!"})
        self.assertEqual(reset.json(), {"status": "password_reset"})

        # Not auto-logged-in -- session was flushed server-side.
        self.assertNotIn("_auth_user_id", self.client.session)

        # Old password no longer works; new password does, via the real
        # login endpoint (full loop back to Flow B).
        old_login = self.client.post("/login/", {"email": "flowd@example.com", "password": "Old-Passphrase1!"})
        self.assertIn(b"Invalid email or password", old_login.content)

        new_login = self.client.post("/login/", {"email": "flowd@example.com", "password": "New-Passphrase-99!"})
        self.assertEqual(new_login.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class FlowEAuthenticatedNavigation(TestCase):
    """FLOW E: login -> home -> profile -> logout -> public state."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="flowe@example.com", email="flowe@example.com", password="Flow-E-Passphrase1!"
        )

    def test_authenticated_navigation_then_logout_returns_to_guest_state(self):
        self.client.post("/login/", {"email": "flowe@example.com", "password": "Flow-E-Passphrase1!"})
        self.assertIn("_auth_user_id", self.client.session)

        profile_resp = self.client.get("/profile/")
        self.assertEqual(profile_resp.status_code, 200)

        # PHASE 8 ARCHITECTURE RECOVERY -- Priority 1: logout is POST-only
        # now (see accounts/tests/test_phase8_nav_logout_safety.py for why:
        # a GET-triggerable logout was reachable by more than the real
        # Logout control -- prefetch, a stray future href, etc). The real
        # nav control is a <form method="post">, so the flow this test is
        # proving (nav -> real logout click -> guest state) is a POST.
        logout_resp = self.client.post("/logout/")
        self.assertEqual(logout_resp.status_code, 302)
        self.assertEqual(logout_resp.url, "/home/")
        self.assertNotIn("_auth_user_id", self.client.session)

        # A logged-out session hitting a login-required page is redirected
        # to log in, not served the protected page (confirms logout was
        # real, not just a redirect with the session still valid).
        profile_after_logout = self.client.get("/profile/")
        self.assertEqual(profile_after_logout.status_code, 302)
        self.assertIn("/login/", profile_after_logout.url)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class FlowFSessionEdgeCases(TestCase):
    """FLOW F: expired/missing session states across every auth page."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="flowf@example.com", email="flowf@example.com", password="Flow-F-Passphrase1!"
        )

    def test_opening_otp_page_without_signup_session_redirects(self):
        resp = self.client.get("/verify-otp/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/signup/")

    def test_verify_otp_post_without_any_session_state(self):
        resp = self.client.post("/verify-otp/", {"otp": "123456"})
        self.assertEqual(resp.json(), {"status": "error", "message": "Session expired"})

    def test_resend_otp_without_signup_session(self):
        resp = self.client.post("/resend-otp/")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Session expired")

    def test_forgot_verify_otp_without_send_first(self):
        resp = self.client.post("/forgot/verify-otp/", {"otp": "123456"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Session expired")

    def test_reset_password_without_verified_otp(self):
        resp = self.client.post("/forgot/reset-password/", {"password": "Whatever-Passphrase1!"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "OTP not verified")

    def test_already_authenticated_user_visiting_login_still_renders_form(self):
        # The backend has no "already authenticated" redirect on this
        # view -- confirming that (not inventing one) rather than
        # assuming a behavior that doesn't exist.
        self.client.login(username="flowf@example.com", password="Flow-F-Passphrase1!")
        resp = self.client.get("/login/")
        self.assertEqual(resp.status_code, 200)

    def test_already_authenticated_user_visiting_signup_still_renders_form(self):
        self.client.login(username="flowf@example.com", password="Flow-F-Passphrase1!")
        resp = self.client.get("/signup/")
        self.assertEqual(resp.status_code, 200)

    @patch("accounts.views.send_registration_otp")
    def test_expired_pending_signup_forces_restart(self, mock_send):
        from django.utils import timezone
        from datetime import timedelta

        self.client.post(
            "/signup/",
            {
                "name": "Flow F2",
                "email": "flowf2@example.com",
                "password": "Flow-F2-Passphrase1!",
                "confirmPassword": "Flow-F2-Passphrase1!",
            },
        )
        pending = PendingSignup.objects.get(email="flowf2@example.com")
        pending.created_at = timezone.now() - timedelta(minutes=16)
        pending.save(update_fields=["created_at"])

        otp = EmailOTP.objects.get(email="flowf2@example.com", purpose="signup").otp
        resp = self.client.post("/verify-otp/", {"otp": otp})
        self.assertEqual(resp.json()["status"], "error")
        self.assertIn("Please sign up again", resp.json()["message"])
        self.assertFalse(PendingSignup.objects.filter(email="flowf2@example.com").exists())
