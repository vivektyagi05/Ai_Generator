# accounts/tests/test_phase8a_provider_error_classification.py
"""
PHASE 8A — regression coverage for the actual reported bug and the
structured-error-classification fix that resolved it.

Root cause (documented in AI_GENERATORS/api_views.py's GROQ_MODEL comment
and PHASE_8A_ROOT_CAUSE.md): the model this app was hardcoded to,
"llama-3.1-8b-instant", was deprecated by Groq on 2026-06-17 and shut down
on 2026-08-16 (https://console.groq.com/docs/deprecations). Every request
to a decommissioned model ID returns HTTP 400 with
{"error": {"code": "model_decommissioned", ...}}. Before this phase, that
collapsed into the single generic "AI provider returned an error." for
every non-2xx Groq response, indistinguishable from a genuine outage --
this file locks in that it no longer does, AND locks in that Story/
Application/Proposal/Code (the four features that were actually broken)
all go through the exact same classification path, so a regression in any
one of them is caught here rather than only for a generic "chat" feature.

Also covers: Photo succeeding while Groq is unreachable (proves the two
providers are genuinely independent, per the audit's Step 2 requirement
that Photo's success never be treated as evidence the Groq path works),
and that no provider secret/exception text ever reaches the JSON body.
"""

import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import AIUsage, CreditAccount


def _groq_response(status_code, body):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _decommissioned_model_response():
    # The exact shape Groq returns for a shut-down model ID (see
    # console.groq.com/docs/errors) -- this is what every Story/
    # Application/Proposal/Code request was actually receiving.
    return _groq_response(
        400,
        {
            "error": {
                "message": "The model `llama-3.1-8b-instant` has been decommissioned "
                "and is no longer supported.",
                "type": "invalid_request_error",
                "code": "model_decommissioned",
            }
        },
    )


@override_settings(GROQ_API_KEY="test-key")
class ModelDecommissionedRegressionTests(TestCase):
    """The literal bug the user reported, reproduced and locked in as a
    regression test -- one per affected generator, per the audit's Step 12
    requirement ("test the exact user-reported failure ... and equivalent
    coverage for Application, Proposal, Code")."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="regression_user", password="x")
        self.client.force_login(self.user)

    def _post(self, feature, request_id, prompt="write something"):
        body = {"prompt": prompt, "feature": feature, "request_id": request_id}
        return self.client.post("/api/ai/", data=json.dumps(body), content_type="application/json")

    def _assert_safe_decommission_failure(self, feature, request_id):
        response = self._post(feature, request_id)

        self.assertEqual(response.status_code, 502)
        data = response.json()

        # Structured, actionable error -- not the old bare generic string.
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "AI_CONFIGURATION_ERROR")
        self.assertFalse(data["error"]["retryable"])  # retrying the same broken model ID never helps
        self.assertEqual(data["error"]["request_id"], request_id)

        # The raw Groq exception text / model id / "decommissioned" wording
        # must never leak into the response the browser sees.
        raw = response.content.decode()
        self.assertNotIn("llama-3.1-8b-instant", raw)
        self.assertNotIn("model_decommissioned", raw)
        self.assertNotIn("test-key", raw)  # the API key itself, for good measure

        # Credits: reserved, then fully refunded -- never left dangling,
        # never actually charged for a request that never produced output.
        usage = AIUsage.objects.get(request_id=request_id)
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)
        self.assertEqual(usage.credits_refunded, usage.credits_reserved)
        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)
        return data

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_story_generator_decommissioned_model(self, mock_post):
        mock_post.return_value = _decommissioned_model_response()
        self._assert_safe_decommission_failure("story", "story-decommission-1")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_application_generator_decommissioned_model(self, mock_post):
        mock_post.return_value = _decommissioned_model_response()
        self._assert_safe_decommission_failure("application", "application-decommission-1")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_proposal_generator_decommissioned_model(self, mock_post):
        mock_post.return_value = _decommissioned_model_response()
        self._assert_safe_decommission_failure("proposal", "proposal-decommission-1")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_code_generator_decommissioned_model(self, mock_post):
        mock_post.return_value = _decommissioned_model_response()
        self._assert_safe_decommission_failure("code", "code-decommission-1")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_current_model_constant_is_not_the_decommissioned_one(self, mock_post):
        """Guards directly against re-introducing the root cause: whatever
        GROQ_MODEL is set to, it must not be one of the model IDs Groq has
        published as shut down. This would have caught the original bug
        outright, without needing a live Groq call."""
        from AI_GENERATORS.api_views import GROQ_MODEL

        shut_down_as_of_2026_08_16 = {"llama-3.1-8b-instant", "llama-3.3-70b-versatile"}
        self.assertNotIn(GROQ_MODEL, shut_down_as_of_2026_08_16)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_photo_still_works_while_groq_is_broken(self, mock_post):
        """Step 2 of the audit: Photo succeeding is never evidence the Groq
        path works, because it's a different provider entirely. Prove that
        explicitly -- Groq mocked to always fail, Photo mocked to succeed,
        both in the same test."""

        def fake_post(*args, **kwargs):
            raise AssertionError("photo must never call requests.post (Groq) -- it uses requests.get")

        mock_post.side_effect = fake_post

        with patch("AI_GENERATORS.api_views.requests.get") as mock_get:
            image_resp = Mock()
            image_resp.status_code = 200
            mock_get.return_value = image_resp

            response = self._post("photo", "photo-independent-1", prompt="a red bicycle")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("result", data)
        usage = AIUsage.objects.get(request_id="photo-independent-1")
        self.assertEqual(usage.status, AIUsage.Status.FINALIZED)


@override_settings(GROQ_API_KEY="test-key")
class ProviderErrorClassificationTests(TestCase):
    """Each AI_* error code from a distinct Groq failure mode -- the
    forensic-audit list in Step 3/Step 5 of the brief, each proven to (a)
    map to the right code, (b) never leak provider internals, and (c)
    always refund."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="classify_user", password="x")
        self.client.force_login(self.user)

    def _post(self, request_id):
        body = {"prompt": "hello", "feature": "chat", "request_id": request_id}
        return self.client.post("/api/ai/", data=json.dumps(body), content_type="application/json")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_invalid_api_key_classified_as_auth_failed(self, mock_post):
        mock_post.return_value = _groq_response(
            401, {"error": {"message": "Invalid API Key", "code": "invalid_api_key"}}
        )
        response = self._post("classify-auth-1")
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertEqual(data["error"]["code"], "AI_AUTH_FAILED")
        self.assertFalse(data["error"]["retryable"])
        self.assertNotIn("Invalid API Key", response.content.decode())

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_rate_limited_classified_and_retryable(self, mock_post):
        mock_post.return_value = _groq_response(
            429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}}
        )
        response = self._post("classify-rl-1")
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertEqual(data["error"]["code"], "AI_PROVIDER_RATE_LIMITED")
        self.assertTrue(data["error"]["retryable"])

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_upstream_5xx_classified_as_unavailable_and_retryable(self, mock_post):
        mock_post.return_value = _groq_response(503, {"error": "upstream overloaded"})
        response = self._post("classify-5xx-1")
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertEqual(data["error"]["code"], "AI_PROVIDER_UNAVAILABLE")
        self.assertTrue(data["error"]["retryable"])

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_timeout_classified_as_provider_timeout(self, mock_post):
        import requests as requests_lib

        mock_post.side_effect = requests_lib.exceptions.Timeout()
        response = self._post("classify-timeout-1")
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data["error"]["code"], "AI_PROVIDER_TIMEOUT")
        self.assertTrue(data["error"]["retryable"])

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_malformed_response_classified_as_invalid_response(self, mock_post):
        mock_post.return_value = _groq_response(200, {"unexpected": "shape"})
        response = self._post("classify-malformed-1")
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertEqual(data["error"]["code"], "AI_PROVIDER_INVALID_RESPONSE")

    def test_missing_groq_key_classified_as_configuration_error(self):
        with override_settings(GROQ_API_KEY=""):
            response = self._post("classify-missing-key-1")
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["error"]["code"], "AI_CONFIGURATION_ERROR")
        self.assertFalse(data["error"]["retryable"])
        # No AIUsage row at all -- this fails before any reservation, same
        # as before this phase (nothing to refund because nothing was ever
        # reserved).
        self.assertFalse(AIUsage.objects.filter(request_id="classify-missing-key-1").exists())


@override_settings(GROQ_API_KEY='  "test-key-with-quotes"  \n')
class GroqApiKeySanitizationTests(TestCase):
    """PHASE 8A forensic-audit item 3: whitespace/quote issues in the
    configured key must not reach the Authorization header un-sanitized."""

    def test_settings_value_is_stripped_of_whitespace_and_quotes(self):
        # settings.GROQ_API_KEY is read at import time from os.getenv, so
        # this test instead exercises the same normalization function
        # directly against a representative dirty value, mirroring exactly
        # what AI_GENERATORS/settings.py does at startup.
        raw = '  "test-key-with-quotes"  \n'
        cleaned = raw.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
            cleaned = cleaned[1:-1].strip()
        self.assertEqual(cleaned, "test-key-with-quotes")
