"""
PHASE 4B closure — AI credit-exhaustion UI integration tests.

Covers what this closure pass actually added/changed:

  * The exact backend contract templates/main.html's new
    renderAiFailure()/isExhaustionError() JS relies on: /api/ai/'s 402
    (InsufficientCreditsError) and 429 QUOTA_EXCEEDED response shapes,
    unchanged backend behavior re-verified here so a future backend edit
    that silently changes either shape gets caught.
  * That templates/main.html actually wires window.BillingFlow (loads
    static/js/billing.js, references it once, doesn't duplicate the
    create-order/verify-payment/polling logic).
  * That the escapeHtml() scoping bug found during the forensic audit
    (declared only inside generateCode(), so generateStory/
    generateApplication/generateProposal's own calls to it threw a
    ReferenceError on every failure) is fixed -- exactly one definition,
    reachable from the whole script.
  * A premium (already-upgraded) user's page doesn't ship an unnecessary
    exhaustion affordance for a request that actually succeeds.

What is and isn't executable here: Django's test client renders the
template server-side and inspects the emitted HTML/JS text -- it does not
execute JavaScript (no browser engine available in this environment,
per PHASE_4B_MANUAL_E2E_CHECKLIST.md). So "frontend identifies credit
exhaustion" below is verified as "the exact response the frontend's
isExhaustionError()/renderAiFailure() functions are written against, and
those functions are present in the shipped page," not as an actual
executed click-through. Genuine browser behavior (button disabling
in real time, the Razorpay modal, duplicate-click suppression in a live
DOM) is out of reach here and is left to the manual checklist, not claimed
as passed.
"""

import json
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import CreditAccount
from accounts.services import credit_service, subscription_service
from accounts.services.entitlement_service import AccessDecision, DenialReason


# PHASE 8A -- forensic-audit finding: this file's three test classes never
# set GROQ_API_KEY, unlike every other file that posts to /api/ai/ with a
# non-photo feature (see test_ai_credit_integration.py,
# test_phase8a_generator_reverification.py, etc., all of which carry
# @override_settings(GROQ_API_KEY="test-key")). That meant every test below
# silently depended on a leftover local .env's GROQ_API_KEY to reach 402/
# 429/403/200 at all -- in a clean checkout or CI runner with no .env, the
# view's "GROQ_API_KEY missing" guard fires FIRST (before the entitlement
# check these tests are actually exercising) and every one of them gets an
# unconditional 500 instead of the status code it asserts. That is exactly
# the kind of "tests pass on my machine, fail for everyone else" gap this
# phase's audit was asked to find -- fixed by giving these classes the same
# override_settings() the rest of the suite already uses.
@override_settings(GROQ_API_KEY="test-key")
class CreditExhaustionContractTests(TestCase):
    """
    Test 1/2 from the brief's Step 14 list: confirm the exact response the
    frontend must react to, using the real backend path (no mocking of
    credit_service itself) -- draining the ledger the same way any real
    user would, then hitting the real view.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="exhausted", password="x")
        self.client.force_login(self.user)

    def _post(self, **overrides):
        body = {"prompt": "hello", "feature": "chat", "request_id": uuid.uuid4().hex}
        body.update(overrides)
        return self.client.post("/api/ai/", data=json.dumps(body), content_type="application/json")

    def test_zero_balance_returns_402_with_plain_string_error(self):
        account = credit_service.get_or_create_account(user=self.user)
        CreditAccount.objects.filter(pk=account.pk).update(balance=0, reserved_balance=0)

        response = self._post()

        self.assertEqual(response.status_code, 402)
        body = response.json()
        # This is the exact shape templates/main.html's extractApiError()
        # branches on: a plain string, not an {code, message} object.
        self.assertIsInstance(body["error"], str)
        self.assertIn("request_id", body)

    def test_quota_exceeded_returns_429_with_structured_error(self):
        denial = AccessDecision(allowed=False, reason=DenialReason.QUOTA_EXCEEDED, plan="FREE", feature="chat", remaining=0)
        with patch("AI_GENERATORS.api_views.entitlement_service.get_access_decision", return_value=denial):
            response = self._post()

        self.assertEqual(response.status_code, 429)
        body = response.json()
        # This is the OTHER shape extractApiError() must also handle --
        # an object with .code/.message, not a plain string.
        self.assertIsInstance(body["error"], dict)
        self.assertEqual(body["error"]["code"], "QUOTA_EXCEEDED")

    def test_plan_not_allowed_returns_403_with_structured_error(self):
        denial = AccessDecision(allowed=False, reason=DenialReason.PLAN_NOT_ALLOWED, plan="FREE", feature="chat", remaining=0)
        with patch("AI_GENERATORS.api_views.entitlement_service.get_access_decision", return_value=denial):
            response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "PLAN_NOT_ALLOWED")

    def test_authentication_required_returns_401(self):
        anon = Client()
        denial = AccessDecision(allowed=False, reason=DenialReason.AUTHENTICATION_REQUIRED, plan="GUEST", feature="chat", remaining=0)
        with patch("AI_GENERATORS.api_views.entitlement_service.get_access_decision", return_value=denial):
            response = anon.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "feature": "chat", "request_id": uuid.uuid4().hex}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 401)


class MainPageBillingWiringTests(TestCase):
    """
    Tests 3/4/9/10 from the brief's Step 14 list, at the level actually
    verifiable server-side: the CTA and its wiring exist in the shipped
    page and reuse the one BillingFlow, rather than asserting live click
    behavior no tool here can execute.
    """

    def setUp(self):
        self.client = Client()

    def test_main_page_loads_billing_flow_script_once(self):
        response = self.client.get("/home/")
        html = response.content.decode()
        # Exactly one <script src=".../billing.js"> -- reused, not
        # duplicated (Step 4).
        self.assertEqual(html.count('src="/static/js/billing.js"'), 1)

    def test_main_page_defines_exhaustion_card_and_uses_billing_flow(self):
        html = self.client.get("/home/").content.decode()
        self.assertIn("function renderExhaustionCard", html)
        self.assertIn("window.BillingFlow.create", html)
        # The exhaustion CTA must not be a dead link or a fake handler.
        self.assertNotIn('href="#"', html)
        self.assertNotIn("alert(\"Upgrade", html)
        self.assertNotIn("Coming Soon", html)

    def test_escape_html_defined_exactly_once(self):
        # Forensic-audit finding: escapeHtml() used to be nested inside
        # generateCode() only, so generateStory/generateApplication/
        # generateProposal's calls to it threw a ReferenceError on every
        # single failure path. Fixed by hoisting to top-level scope; this
        # asserts it stays fixed (a regression back to a nested/duplicate
        # definition would silently break failure rendering for 3 of 4
        # generators again).
        html = self.client.get("/home/").content.decode()
        self.assertEqual(html.count("function escapeHtml"), 1)

    def test_no_client_side_premium_flag_assignment(self):
        html = self.client.get("/home/").content.decode()
        for forbidden in ("isPremium = true", "is_premium = true", "premium = true"):
            self.assertNotIn(forbidden, html.replace("isPremium=true", "isPremium = true"))

    def test_success_state_confirms_via_entitlements_api_not_assumed(self):
        html = self.client.get("/home/").content.decode()
        # onExhaustionUpgradeSuccess() must re-derive access from the
        # server, not just flip UI state on BillingFlow's SUCCESS callback.
        self.assertIn("/api/entitlements/", html)


@override_settings(GROQ_API_KEY="test-key")
class PremiumUserNoUnnecessaryExhaustionTests(TestCase):
    """Test 10: a Premium user's real request succeeding shouldn't need
    (and won't trigger) the exhaustion path at all -- 200, not 402/429."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="premai", password="x")
        from accounts.models import Plan

        plan = Plan.objects.get(code="PREMIUM")
        sub = subscription_service.create_subscription(
            user=self.user, plan=plan, billing_interval="MONTHLY", performed_by=self.user, source="test"
        )
        subscription_service.activate_subscription(subscription=sub, performed_by=self.user, source="test")
        self.client.force_login(self.user)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_premium_user_request_succeeds_not_exhausted(self, mock_post):
        from unittest.mock import Mock

        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        mock_post.return_value = resp

        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hello", "feature": "chat", "request_id": uuid.uuid4().hex}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
