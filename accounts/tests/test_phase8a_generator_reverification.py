"""
PHASE 8A -- Item 3: re-verification of the four generators reported as
"not working" (Story, Application, Proposal, Code), after the auth/
credit boundary fix in Item 2.

This complements test_generator_experience_matrix.py /
test_generator_lifecycle.py (which assert the shared-controller/template
*contract*: correct feature slugs, correct state objects, correct
history-save calls -- all via rendered-template inspection, since no
browser is available in this environment to actually click a button) by
proving the *runtime* contract holds too: a real Client() POST to the
real /api/ai/ view, with only the outbound Groq HTTP call mocked,
produces a 200 with the exact response shape the frontend's
runGeneratorLifecycle() consumes (result / credits_charged /
credits_available -- see templates/main.html's aiRequest() caller and
onSuccess handlers for each generator), and that credits are actually,
sequentially deducted across generators from one shared account -- not
per-generator silos.

No divergence was found for any of the four: every one already used the
canonical feature slug (accounts/ai_request_config.py's
ALLOWED_FEATURES: "chat,code,story,application,proposal,photo", matching
templates/main.html's TAB_ID_TO_GENERATOR_PARAM exactly), the shared
runGeneratorLifecycle()/aiRequest() controller, and correctly-scoped,
non-orphaned button handlers (verified by direct inspection: each of
generateCode/generateStory/generateApplication/generateProposal is
declared exactly once at module scope and wired via a matching `onclick`
on its own Generate button; no duplicate declarations, no dead
references). Photo is re-verified separately in
test_ai_photo_integration.py (uses the image provider, not Groq) --
not duplicated here.
"""

import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings


def _groq_success_response(content):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }
    return resp


@override_settings(GROQ_API_KEY="test-key")
class TextGeneratorReverificationTests(TestCase):
    """Story, Application, Proposal, Code -- the four reported broken."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="reverifyuser", email="reverify@example.com", password="pw12345!"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_each_generator_succeeds_with_the_response_shape_the_frontend_consumes(self):
        for feature in ["code", "story", "application", "proposal"]:
            with patch(
                "AI_GENERATORS.api_views.requests.post",
                return_value=_groq_success_response(f"result for {feature}"),
            ):
                response = self.client.post(
                    "/api/ai/",
                    data=json.dumps(
                        {"prompt": "test input", "feature": feature, "request_id": f"reverify-{feature}"}
                    ),
                    content_type="application/json",
                )
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            # Exact keys templates/main.html's runGeneratorLifecycle()
            # reads: result (-> config.onSuccess), credits_charged /
            # credits_available (-> refreshCreditPill()).
            self.assertIn("result", body)
            self.assertIn("credits_charged", body)
            self.assertIn("credits_available", body)
            self.assertEqual(body["result"], f"result for {feature}")

    def test_credits_deduct_sequentially_from_one_shared_account_across_generators(self):
        balances = []
        for feature in ["code", "story", "application", "proposal"]:
            with patch(
                "AI_GENERATORS.api_views.requests.post",
                return_value=_groq_success_response("x"),
            ):
                response = self.client.post(
                    "/api/ai/",
                    data=json.dumps(
                        {"prompt": "hi", "feature": feature, "request_id": f"reverify-seq-{feature}"}
                    ),
                    content_type="application/json",
                )
            balances.append(response.json()["credits_available"])
        # Strictly decreasing -- one shared CreditAccount, not a fresh
        # grant or an isolated silo per generator.
        self.assertEqual(balances, sorted(balances, reverse=True))
        self.assertEqual(len(set(balances)), len(balances))
