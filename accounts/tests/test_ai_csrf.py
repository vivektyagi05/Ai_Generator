"""
PHASE 1A Step 12 — CSRF tests for /api/ai/.

AI_GENERATORS/api_views.py no longer carries @csrf_exempt, so these tests
use Client(enforce_csrf_checks=True) -- the default test Client disables
CSRF checking entirely, which would make these tests pass regardless of
whether the view is actually protected.
"""

import json
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings


def _success_response():
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return resp


@override_settings(GROQ_API_KEY="test-key")
class AiCsrfTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_missing_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "csrf-missing-1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_valid_csrf_token_is_accepted(self, mock_post):
        # PHASE 8A: a guest now gets 401 AUTHENTICATION_REQUIRED before
        # CSRF-token acceptance can even be observed as a 200 (guests are
        # denied at the entitlement gate, which runs after CSRF
        # middleware but before this view does anything else -- see
        # entitlement_service._decide()'s guest branch). This test is
        # about CSRF acceptance, not guest policy (that's covered by
        # test_ai_entitlement_integration.py), so it now logs in a user
        # so a valid token's success can actually be observed as a 200.
        from django.contrib.auth.models import User

        mock_post.return_value = _success_response()
        client = Client(enforce_csrf_checks=True)
        user = User.objects.create_user(username="csrfuser", password="pw12345!")
        client.force_login(user)

        # Load a page that renders {{ csrf_token }} (templates/main.html)
        # so Django issues the csrftoken cookie, exactly like a real
        # browser before templates/main.html's aiRequest() helper runs.
        client.get("/home/")
        csrftoken = client.cookies["csrftoken"].value

        response = client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "csrf-valid-1"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrftoken,
        )
        self.assertEqual(response.status_code, 200)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_stale_or_mismatched_csrf_token_is_rejected(self, mock_post):
        mock_post.return_value = _success_response()
        client = Client(enforce_csrf_checks=True)
        client.get("/home/")

        response = client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "csrf-mismatch-1"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN="not-the-real-token",
        )
        self.assertEqual(response.status_code, 403)
