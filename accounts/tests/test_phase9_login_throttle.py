"""
PHASE 9 — Step 3 (auth/session hardening): user_login() previously had
NO throttling of any kind -- unlimited password guesses against any
account. Confirmed by grep across accounts/tests/ before this fix: no
test, and no rate_limit call, ever referenced login brute-force
resistance. Fixed with two independent caps mirroring the pattern
already used elsewhere in this module: per-IP (check_ip_throttle) and
per-email failed-attempt (check_login_throttle/record_failed_login, new
in accounts/rate_limit.py).
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.cache import cache

from accounts.rate_limit import LOGIN_FAIL_MAX_PER_WINDOW, IP_MAX_PER_WINDOW

User = get_user_model()


class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="throttleuser@example.com",
            email="throttleuser@example.com",
            password="correct-horse-battery-staple",
        )

    def _attempt(self, password="wrong-password"):
        return self.client.post(
            "/login/",
            {"email": "throttleuser@example.com", "password": password},
        )

    def test_correct_password_still_logs_in_under_the_cap(self):
        resp = self._attempt(password="correct-horse-battery-staple")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_repeated_wrong_passwords_lock_out_the_account(self):
        for _ in range(LOGIN_FAIL_MAX_PER_WINDOW):
            resp = self._attempt()
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("_auth_user_id", self.client.session)

        # One more, over the cap: must be refused BEFORE authenticate()
        # even runs -- so it must fail even with the CORRECT password.
        resp = self._attempt(password="correct-horse-battery-staple")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Too many failed attempts", resp.content.decode())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_successful_login_does_not_count_as_a_failed_attempt(self):
        # A handful of successes shouldn't burn down the failed-attempt
        # budget -- only wrong passwords should.
        for _ in range(3):
            self.client.post("/logout/") if "_auth_user_id" in self.client.session else None
            resp = self._attempt(password="correct-horse-battery-staple")
            self.assertEqual(resp.status_code, 302)

        # Still plenty of budget left for a genuine wrong-password retry.
        resp = self._attempt()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Invalid email or password", resp.content.decode())

    def test_ip_throttle_caps_total_login_posts_regardless_of_account(self):
        # Different (nonexistent) emails, same client/IP -- the per-IP cap
        # should still eventually kick in.
        last_resp = None
        for i in range(IP_MAX_PER_WINDOW + 1):
            last_resp = self.client.post(
                "/login/",
                {"email": f"nonexistent{i}@example.com", "password": "whatever"},
            )
        self.assertEqual(last_resp.status_code, 200)
        self.assertIn("Too many login attempts", last_resp.content.decode())
