"""
PHASE 1A Step 12 — request validation tests for /api/ai/ (Step 4).

Every one of these must be rejected before a credit reservation is
attempted -- none of these tests need the provider mocked, because a
provider call should never happen for an invalid request.
"""

import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts import ai_request_config
from accounts.models import AIUsage


@override_settings(GROQ_API_KEY="test-key")
class AiRequestValidationTests(TestCase):
    def setUp(self):
        # See test_ai_credit_integration.py setUp for why this is needed --
        # the guest/IP rate limiter lives in Django's cache, not the DB, so
        # it survives across tests unless explicitly cleared here.
        cache.clear()
        self.client = Client()
        # PHASE 8A: anonymous requests are now denied at the entitlement
        # gate (AUTHENTICATION_REQUIRED) before request_id/prompt-length
        # validation ever runs (auth is checked before payload shape --
        # see entitlement_service._decide()'s guest branch and the
        # canonical gate order it documents). This file is about payload
        # validation, not the auth/guest policy (that's covered by
        # test_ai_entitlement_integration.py), so requests here now run
        # as an authenticated user to actually reach the validation code
        # under test instead of being confounded by the (correct) guest
        # denial.
        self.user = User.objects.create_user(username="validationuser", password="pw12345!")
        self.client.force_login(self.user)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_malformed_json_rejected(self, mock_post):
        response = self.client.post("/api/ai/", data="{not valid json", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_non_object_json_rejected(self, mock_post):
        response = self.client.post("/api/ai/", data=json.dumps(["a", "list"]), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_oversized_body_rejected(self, mock_post):
        huge_prompt = "a" * (ai_request_config.MAX_BODY_BYTES + 1000)
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": huge_prompt, "request_id": "oversize-body"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)
        mock_post.assert_not_called()
        self.assertFalse(AIUsage.objects.filter(request_id="oversize-body").exists())

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_oversized_prompt_rejected(self, mock_post):
        # Under the body-size cap but over the prompt-char cap.
        long_prompt = "a" * (ai_request_config.MAX_PROMPT_CHARS + 1)
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": long_prompt, "request_id": "oversize-prompt"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_invalid_feature_rejected(self, mock_post):
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "feature": "not-a-real-feature", "request_id": "bad-feature"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_invalid_content_type_rejected(self, mock_post):
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "bad-ctype"}),
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_invalid_request_id_shape_rejected(self, mock_post):
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "not valid! spaces/slashes\\"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_overlong_request_id_shape_rejected(self, mock_post):
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "a" * 65}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_valid_feature_accepted(self, mock_post):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        mock_post.return_value = resp

        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "feature": "code", "request_id": "good-feature"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_omitted_feature_defaults_to_chat(self, mock_post):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        mock_post.return_value = resp

        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "no-feature-given"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        usage = AIUsage.objects.get(request_id="no-feature-given")
        self.assertEqual(usage.feature, "chat")
