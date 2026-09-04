# accounts/tests/test_ai_photo_integration.py
"""
PHASE 1 — GENERATOR ARCHITECTURE NORMALIZATION.

Mirrors accounts/tests/test_ai_credit_integration.py's structure/patterns
exactly, but for the "photo" feature and its image provider
(_call_image_provider() in AI_GENERATORS/api_views.py) instead of Groq.
Every test here exists because Step 7 of the phase task asked for it by
name.
"""
import json
from unittest.mock import Mock, patch

import requests as requests_lib
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts import credit_config
from accounts.models import AIUsage, CreditAccount, Feature


def _image_provider_success_response():
    resp = Mock()
    resp.status_code = 200
    resp.close = Mock()
    return resp


def _image_provider_error_response(status_code=502):
    resp = Mock()
    resp.status_code = status_code
    resp.json.side_effect = ValueError()
    resp.text = "bad gateway"
    resp.close = Mock()
    return resp


class PhotoFeatureRegistrationTests(TestCase):
    """Step 7.1 — feature registration."""

    def test_photo_is_an_allowed_feature(self):
        from accounts import ai_request_config

        self.assertIn("photo", ai_request_config.ALLOWED_FEATURES)

    def test_photo_feature_row_exists_and_is_active(self):
        feature = Feature.objects.get(code="AI_PHOTO")
        self.assertTrue(feature.is_active)


@override_settings(GROQ_API_KEY="")  # deliberately unset -- photo must not need it
class PhotoGenerationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="ada", password="x")
        self.client.force_login(self.user)

    def _post(self, **body_overrides):
        body = {"prompt": "a cyberpunk warrior in the rain", "feature": "photo"}
        body.update(body_overrides)
        return self.client.post("/api/ai/", data=json.dumps(body), content_type="application/json")

    # Step 7.2 — auth requirement: photo does NOT require Groq's key.
    def test_photo_does_not_require_groq_api_key(self):
        with patch("AI_GENERATORS.api_views.requests.get") as mock_get:
            mock_get.return_value = _image_provider_success_response()
            response = self._post(request_id="req-photo-no-groq-key")
        self.assertEqual(response.status_code, 200)

    # Step 7.3 / 7.5 — credit reservation + successful generation + commit.
    @patch("AI_GENERATORS.api_views.requests.get")
    def test_successful_generation_charges_flat_cost_and_returns_url(self, mock_get):
        mock_get.return_value = _image_provider_success_response()

        response = self._post(request_id="req-photo-success")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("image.pollinations.ai/prompt/", data["result"])
        self.assertEqual(data["credits_charged"], credit_config.IMAGE_GENERATION_CREDIT_COST)

        usage = AIUsage.objects.get(request_id="req-photo-success")
        self.assertEqual(usage.status, AIUsage.Status.FINALIZED)
        self.assertEqual(usage.feature, "photo")
        self.assertEqual(usage.provider, "pollinations")
        self.assertTrue(usage.usage_unavailable)  # no token metric for images -- by design
        self.assertEqual(usage.credits_charged, credit_config.IMAGE_GENERATION_CREDIT_COST)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)  # nothing left dangling reserved

    # The prompt is server-side URL-encoded, never trusted verbatim into a URL.
    @patch("AI_GENERATORS.api_views.requests.get")
    def test_generated_url_is_built_server_side_from_the_prompt(self, mock_get):
        mock_get.return_value = _image_provider_success_response()

        response = self._post(prompt="a red panda in space", request_id="req-photo-url")

        self.assertEqual(response.status_code, 200)
        called_url = mock_get.call_args[0][0]
        self.assertIn("a%20red%20panda%20in%20space", called_url)
        self.assertEqual(response.json()["result"], called_url)

    # Step 7.6 — provider failure -> Step 7.7 credit rollback.
    @patch("AI_GENERATORS.api_views.requests.get")
    def test_provider_http_failure_refunds(self, mock_get):
        mock_get.return_value = _image_provider_error_response(502)

        response = self._post(request_id="req-photo-fail-http")
        self.assertEqual(response.status_code, 502)

        usage = AIUsage.objects.get(request_id="req-photo-fail-http")
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)
        self.assertEqual(usage.credits_refunded, usage.credits_reserved)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)

    @patch("AI_GENERATORS.api_views.requests.get")
    def test_provider_timeout_refunds(self, mock_get):
        mock_get.side_effect = requests_lib.exceptions.Timeout()

        response = self._post(request_id="req-photo-timeout")
        self.assertEqual(response.status_code, 503)

        usage = AIUsage.objects.get(request_id="req-photo-timeout")
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)
        self.assertEqual(usage.error_code, "provider_timeout")

    # Step 7.8 — insufficient credits.
    def test_insufficient_credits_blocks_request_before_any_provider_call(self):
        CreditAccount.objects.create(user=self.user, balance=0)

        with patch("AI_GENERATORS.api_views.requests.get") as mock_get:
            response = self._post(request_id="req-photo-broke")
            mock_get.assert_not_called()

        self.assertEqual(response.status_code, 402)
        usage = AIUsage.objects.get(request_id="req-photo-broke")
        self.assertEqual(usage.status, AIUsage.Status.INSUFFICIENT_CREDITS)

    # Step 7.9 — entitlement denial (feature disabled for the plan).
    @patch("AI_GENERATORS.api_views.requests.get")
    def test_feature_disabled_denies_before_any_provider_call(self, mock_get):
        from accounts.models import Plan, PlanFeatureRule

        free_plan = Plan.objects.get(code="FREE")
        photo_feature = Feature.objects.get(code="AI_PHOTO")
        PlanFeatureRule.objects.filter(plan=free_plan, feature=photo_feature).update(
            access=PlanFeatureRule.Access.DISABLED
        )

        response = self._post(request_id="req-photo-disabled")
        self.assertEqual(response.status_code, 403)
        mock_get.assert_not_called()
        self.assertFalse(AIUsage.objects.filter(request_id="req-photo-disabled").exists())

    # Step 7.11 — history persistence (existing generic /save_history/ path).
    @patch("AI_GENERATORS.api_views.requests.get")
    def test_successful_generation_can_be_saved_to_history(self, mock_get):
        mock_get.return_value = _image_provider_success_response()

        gen_response = self._post(request_id="req-photo-history")
        image_url = gen_response.json()["result"]

        save_response = self.client.post(
            "/save_history/",
            data=json.dumps({"query": "Photo Prompt: a cyberpunk warrior", "response": image_url}),
            content_type="application/json",
        )
        self.assertEqual(save_response.status_code, 200)

    # Step 7.12 — client cannot override the server-side credit cost.
    @patch("AI_GENERATORS.api_views.requests.get")
    def test_client_supplied_cost_fields_are_ignored(self, mock_get):
        mock_get.return_value = _image_provider_success_response()

        response = self._post(
            request_id="req-photo-spoof-cost",
            credits_charged=1,
            cost=0,
            credits=999999999,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["credits_charged"], credit_config.IMAGE_GENERATION_CREDIT_COST)

    # Step 7.13 — existing four generators remain unaffected by this phase.
    @patch("AI_GENERATORS.api_views.requests.post")
    def test_story_feature_is_unaffected_by_photo_changes(self, mock_post):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "once upon a time"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_post.return_value = resp

        with override_settings(GROQ_API_KEY="test-key"):
            response = self.client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "a story", "feature": "story", "request_id": "req-story-unaffected"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        usage = AIUsage.objects.get(request_id="req-story-unaffected")
        self.assertEqual(usage.provider, "groq")
        self.assertFalse(usage.usage_unavailable)  # still token-billed, unlike photo


@override_settings(GROQ_API_KEY="")
class PhotoRegressionTests(TestCase):
    """
    Step 7 regression test: "Photo must NOT be callable directly from
    browser code." There is no client-side codepath left that can reach
    the image provider without first passing through /api/ai/'s
    entitlement + credit reservation -- this asserts the negative directly
    against the template, since a purely backend test can't prove a
    browser-side removal.
    """

    def test_main_html_no_longer_calls_the_image_provider_directly(self):
        import pathlib

        template_path = (
            pathlib.Path(__file__).resolve().parent.parent.parent / "templates" / "main.html"
        )
        content = template_path.read_text(encoding="utf-8")
        self.assertNotIn("image.pollinations.ai", content)
        self.assertIn('feature: "photo"', content)
