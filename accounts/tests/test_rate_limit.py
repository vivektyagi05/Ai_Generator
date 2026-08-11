from django.core.cache import cache
from django.test import TestCase, RequestFactory, override_settings

from accounts import rate_limit


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class RateLimitUnitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_cooldown_blocks_immediate_second_send(self):
        self.assertTrue(rate_limit.check_send_cooldown("a@example.com", "signup"))
        rate_limit.record_send("a@example.com", "signup")
        self.assertFalse(rate_limit.check_send_cooldown("a@example.com", "signup"))

    def test_cooldown_is_per_email(self):
        rate_limit.record_send("a@example.com", "signup")
        self.assertTrue(rate_limit.check_send_cooldown("b@example.com", "signup"))

    def test_cooldown_is_per_purpose(self):
        rate_limit.record_send("a@example.com", "signup")
        self.assertTrue(rate_limit.check_send_cooldown("a@example.com", "reset"))

    def test_window_cap_enforced(self):
        for _ in range(rate_limit.OTP_SEND_MAX_PER_WINDOW):
            self.assertTrue(rate_limit.check_send_window("a@example.com", "signup"))
            rate_limit.record_send("a@example.com", "signup")
        self.assertFalse(rate_limit.check_send_window("a@example.com", "signup"))

    def test_ip_throttle_caps_requests(self):
        request = self.factory.post("/signup/")
        request.META["REMOTE_ADDR"] = "203.0.113.5"
        for _ in range(rate_limit.IP_MAX_PER_WINDOW):
            self.assertTrue(rate_limit.check_ip_throttle(request, "signup"))
        self.assertFalse(rate_limit.check_ip_throttle(request, "signup"))

    def test_ip_throttle_is_per_ip(self):
        r1 = self.factory.post("/signup/")
        r1.META["REMOTE_ADDR"] = "203.0.113.5"
        r2 = self.factory.post("/signup/")
        r2.META["REMOTE_ADDR"] = "203.0.113.6"
        for _ in range(rate_limit.IP_MAX_PER_WINDOW):
            rate_limit.check_ip_throttle(r1, "signup")
        self.assertTrue(rate_limit.check_ip_throttle(r2, "signup"))

    def test_get_client_ip_prefers_forwarded_for(self):
        request = self.factory.post("/signup/")
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.9, 10.0.0.1"
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        self.assertEqual(rate_limit.get_client_ip(request), "198.51.100.9")

    def test_get_client_ip_falls_back_to_remote_addr(self):
        request = self.factory.post("/signup/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        self.assertEqual(rate_limit.get_client_ip(request), "10.0.0.1")
