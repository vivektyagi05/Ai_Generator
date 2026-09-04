"""
PHASE 1A Step 12 — server-side rate limit tests for /api/ai/.

Frontend throttling is not security (Step 3) -- these tests hit the real
view in a loop and assert the server itself eventually returns 429,
independent of anything the client does.
"""

import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts import rate_limit


def _success_response():
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return resp


# Ample credits so the rate-limit cutoff is what's tested, not a coincident
# insufficient-credits cutoff (rate limit is checked before credit
# reservation either way, but this keeps the test's intent unambiguous).
@override_settings(
    GROQ_API_KEY="test-key",
    CREDIT_INITIAL_FREE_GRANT=1_000_000,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class AuthenticatedRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="ada", password="x")
        self.client = Client()
        self.client.force_login(self.user)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_authenticated_user_eventually_gets_429(self, mock_post):
        mock_post.return_value = _success_response()

        for i in range(rate_limit.AI_AUTH_MAX_PER_WINDOW):
            response = self.client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "request_id": f"auth-rl-{i}"}),
                content_type="application/json",
            )
            self.assertNotEqual(response.status_code, 429)

        blocked = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "auth-rl-over"}),
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked)
        # No sensitive info about the limiting mechanism in the body.
        self.assertNotIn("cache", blocked.json().get("error", "").lower())

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_rate_limit_is_per_user(self, mock_post):
        mock_post.return_value = _success_response()
        other = User.objects.create_user(username="bob", password="x")
        other_client = Client()
        other_client.force_login(other)

        for i in range(rate_limit.AI_AUTH_MAX_PER_WINDOW):
            self.client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "request_id": f"auth-rl-peruser-{i}"}),
                content_type="application/json",
            )

        # Ada is now at (or over) her cap; Bob, a different user, is not.
        response = other_client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "auth-rl-bob-1"}),
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 429)


@override_settings(
    GROQ_API_KEY="test-key",
    CREDIT_INITIAL_FREE_GRANT=1_000_000,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class GuestRateLimitTests(TestCase):
    """
    PHASE 8A CANONICAL RULE CHANGE:
      OLD RULE: a guest could make up to AI_GUEST_SESSION_MAX_PER_WINDOW
      (or AI_GUEST_IP_MAX_PER_WINDOW) real requests -- each one reaching
      the provider -- before the rate limiter itself started returning
      429. test_guest_eventually_gets_429 and
      test_guest_ip_cap_bounds_session_cycling asserted exactly that
      boundary.
      NEW CANONICAL RULE: entitlement_service._decide()'s guest branch
      now denies with AUTHENTICATION_REQUIRED (401) BEFORE
      rate_limit.check_ai_rate_limit() is ever called for a guest (see
      AI_GENERATORS/api_views.py:ai_generate()'s ordering) -- so the
      guest-specific rate limiter (AI_GUEST_SESSION_MAX_PER_WINDOW /
      AI_GUEST_IP_MAX_PER_WINDOW) is now unreachable dead-code from this
      endpoint specifically, superseded by a boundary that fires on the
      very first request instead of the Nth. rate_limit.py's guest
      constants/functions are deliberately left in place (not deleted --
      per the Phase 8A guest-infrastructure instruction, guest identity
      and its supporting machinery may still be legitimately used
      elsewhere or reinstated later), just no longer exercised by
      /api/ai/.
      WHY THE OLD ASSERTIONS WERE WRONG: they measured how many free
      provider calls a guest could make before being throttled -- a
      question that no longer applies once the answer is "zero".
      WHICH TESTS WERE UPDATED: both of the above, replaced below with
      assertions that (a) a guest is denied on the very first request
      regardless of how many times they retry, and (b) the provider is
      never reached across many guest attempts -- the actual security
      property the old tests were really guarding, now enforced earlier.
    """

    def setUp(self):
        cache.clear()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_guest_never_reaches_429_because_denied_earlier_every_time(self, mock_post):
        mock_post.return_value = _success_response()
        client = Client()

        for i in range(rate_limit.AI_GUEST_SESSION_MAX_PER_WINDOW + 5):
            response = client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "request_id": f"guest-rl-{i}"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_guest_session_cycling_still_never_reaches_the_provider(self, mock_post):
        """Formerly proved the per-IP cap bounded session-cycling abuse;
        now proves the earlier auth boundary bounds it instead -- no
        amount of session cycling gets a guest past it."""
        mock_post.return_value = _success_response()

        for session_num in range(rate_limit.AI_GUEST_IP_MAX_PER_WINDOW + 5):
            client = Client()  # Django's test client uses REMOTE_ADDR=127.0.0.1 for all of these
            response = client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "request_id": f"guest-ip-{session_num}"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)
        mock_post.assert_not_called()
