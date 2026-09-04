"""
PHASE 7 STEP 7.9 -- plain rate-limit (429, non-quota) denials previously
fell into the generic red-text error branch with no guidance on when to
retry, even though the backend already sends a real Retry-After header
(accounts/rate_limit.py's check_ai_rate_limit(), AI_GENERATORS/api_views.py's
429 branch -- see accounts/tests/test_ai_rate_limit.py for that existing
backend contract, unchanged here). This batch reads that real header and
renders it; nothing here invents a wait time.

Same verification boundary as the rest of this test suite: Django's test
client renders the template server-side and inspects the emitted HTML/JS
text -- it does not execute JavaScript. Verified here: the header is read
from the real response object, carried through aiRequest()'s return value,
and used (not a hardcoded number) by the new render path, which is reached
specifically for a plain 429 and not for the existing QUOTA_EXCEEDED /
credits-exhausted paths (which must keep using their own existing
rendering, not this new one).
"""

from django.test import TestCase


class RateLimitRetryAfterWiringTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_retry_after_header_is_actually_read_from_the_real_response(self):
        self.assertIn('response.headers.get("Retry-After")', self.html)

    def test_retry_after_is_carried_through_the_failure_result_not_dropped(self):
        start = self.html.index("async function aiRequest(")
        end = self.html.index("\n        // Tab Switching", start)
        body = self.html[start:end]
        self.assertIn("retryAfter", body)

    def test_render_rate_limited_is_a_real_module_scope_function(self):
        self.assertIn("function renderRateLimited(", self.html)

    def test_render_rate_limited_never_fabricates_a_wait_time(self):
        start = self.html.index("function renderRateLimited(")
        end = self.html.index("\n        }", start)
        body = self.html[start:end]
        # The wait-time line must be gated on the real retryAfterSeconds
        # argument, not always shown / not a hardcoded number.
        self.assertIn("if (retryAfterSeconds)", body)
        self.assertNotIn("navigator.clipboard", body)

    def test_render_ai_failure_routes_plain_429_to_the_new_rate_limit_renderer(self):
        # PHASE 8 STEP 6: renderAiFailure() now also takes retryFn (real
        # Retry button on generic failures) -- locator string updated to
        # match that intentional signature change; the body assertions
        # below are unchanged.
        start = self.html.index("function renderAiFailure(outputEl, result, retryFn) {")
        end = self.html.index("\n        }", start)
        body = self.html[start:end]
        self.assertIn("result.status === 429", body)
        self.assertIn("renderRateLimited(", body)

    def test_quota_exceeded_429_still_uses_the_existing_exhaustion_card_not_the_new_renderer(self):
        # isExhaustionError() must still be checked BEFORE the new plain-429
        # branch, so QUOTA_EXCEEDED (a 429 too) keeps its own existing,
        # already-tested rendering rather than being silently rerouted.
        start = self.html.index("function renderAiFailure(outputEl, result, retryFn) {")
        end = self.html.index("\n        }", start)
        body = self.html[start:end]
        exhaustion_check_pos = body.index("isExhaustionError(")
        rate_limit_check_pos = body.index("result.status === 429")
        self.assertLess(
            exhaustion_check_pos,
            rate_limit_check_pos,
            "QUOTA_EXCEEDED's isExhaustionError() check must run before the new plain-429 branch",
        )
