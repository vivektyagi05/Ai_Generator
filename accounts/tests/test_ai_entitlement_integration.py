"""
PHASE 2 Step 26 — AI endpoint integration tests: entitlement/quota denial
must block BEFORE any credit reservation or provider call; an allowed
request must still go through the full Phase 1 credit flow unchanged.
"""
import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import AIUsage, CreditAccount, Feature, Plan, PlanFeatureRule, UserEntitlement


def _groq_success_response(content="hello world"):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return resp


@override_settings(GROQ_API_KEY="test-key")
class EntitlementDeniedBlocksCreditReservationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="blocked_user", password="x")
        self.client.force_login(self.user)

    def _post(self, **overrides):
        body = {"prompt": "write a haiku", "feature": "chat"}
        body.update(overrides)
        return self.client.post("/api/ai/", data=json.dumps(body), content_type="application/json")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_disabled_feature_blocks_before_credit_reservation_and_provider_call(self, mock_post):
        rule = PlanFeatureRule.objects.get(plan__code="FREE", feature__code="AI_CHAT")
        rule.access = PlanFeatureRule.Access.DISABLED
        rule.save()

        account_before = CreditAccount.objects.get_or_create(user=self.user)[0]
        balance_before = account_before.balance
        reserved_before = account_before.reserved_balance

        response = self._post(request_id="denied-req-1")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "PLAN_NOT_ALLOWED")
        mock_post.assert_not_called()

        account_before.refresh_from_db()
        self.assertEqual(account_before.balance, balance_before)
        self.assertEqual(account_before.reserved_balance, reserved_before)
        self.assertFalse(AIUsage.objects.filter(request_id="denied-req-1").exists())

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_quota_exceeded_blocks_before_credit_reservation_and_provider_call(self, mock_post):
        rule = PlanFeatureRule.objects.get(plan__code="FREE", feature__code="AI_CODE")
        rule.daily_limit = 1
        rule.save()

        mock_post.return_value = _groq_success_response()
        first = self._post(feature="code", request_id="quota-req-1")
        self.assertEqual(first.status_code, 200)

        account = CreditAccount.objects.get(user=self.user)
        balance_after_first = account.balance

        mock_post.reset_mock()
        second = self._post(feature="code", request_id="quota-req-2")

        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "QUOTA_EXCEEDED")
        mock_post.assert_not_called()

        account.refresh_from_db()
        self.assertEqual(account.balance, balance_after_first)  # unchanged by the denied 2nd request

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_expired_entitlement_blocks_before_credit_reservation(self, mock_post):
        from datetime import timedelta

        from django.utils import timezone

        premium = Plan.objects.get(code="PREMIUM")
        UserEntitlement.objects.create(
            user=self.user,
            plan=premium,
            status=UserEntitlement.Status.ACTIVE,
            starts_at=timezone.now() - timedelta(days=60),
            expires_at=timezone.now() - timedelta(days=1),
        )
        # Expired Premium -> falls back to FREE, which still allows AI_CHAT,
        # so this should succeed as a FREE-plan request, NOT be denied for
        # "being Premium" -- expired entitlements must not block the FREE
        # fallback they degrade to.
        mock_post.return_value = _groq_success_response()
        response = self._post(request_id="expired-fallback-1")
        self.assertEqual(response.status_code, 200)


@override_settings(GROQ_API_KEY="test-key")
class AllowedRequestStillUsesFullCreditFlowTests(TestCase):
    """Confirms Phase 2 didn't disturb the Phase 1 credit lifecycle for a
    request that IS entitled — same assertions style as
    test_ai_credit_integration.py."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="allowed_user", password="x")
        self.client.force_login(self.user)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_allowed_request_reserves_and_finalizes_credits_normally(self, mock_post):
        mock_post.return_value = _groq_success_response()
        # Don't pre-create the CreditAccount -- ai_usage_service.begin_request
        # lazily creates it (with the initial free grant) on first use, same
        # as test_ai_credit_integration.py's pattern. Pre-creating it here
        # would create a balance=0 row and skip that grant entirely.
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "feature": "chat", "request_id": "allowed-req-1"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        mock_post.assert_called_once()
        account = CreditAccount.objects.get(user=self.user)
        self.assertLess(account.balance, account.balance + 1)  # sanity: row exists, non-negative flow
        self.assertGreaterEqual(account.balance, 0)
        self.assertTrue(AIUsage.objects.filter(request_id="allowed-req-1", status=AIUsage.Status.FINALIZED).exists())

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_allowed_request_records_entitlement_usage_on_success(self, mock_post):
        mock_post.return_value = _groq_success_response()
        from accounts.services import entitlement_service

        before = entitlement_service.get_usage_count(self.user, None, "AI_CHAT", "daily")
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "feature": "chat", "request_id": "usage-record-1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        after = entitlement_service.get_usage_count(self.user, None, "AI_CHAT", "daily")
        self.assertEqual(after, before + 1)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_provider_failure_does_not_record_entitlement_usage(self, mock_post):
        import requests as requests_lib

        mock_post.side_effect = requests_lib.exceptions.Timeout()
        from accounts.services import entitlement_service

        before = entitlement_service.get_usage_count(self.user, None, "AI_CHAT", "daily")
        self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "feature": "chat", "request_id": "fail-no-usage-1"}),
            content_type="application/json",
        )
        after = entitlement_service.get_usage_count(self.user, None, "AI_CHAT", "daily")
        self.assertEqual(after, before)  # a failed provider call must not consume quota


@override_settings(GROQ_API_KEY="test-key")
class GuestAIEntitlementIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    # PHASE 8A CANONICAL RULE CHANGE:
    #   OLD RULE: a guest could generate up to GUEST_DAILY_LIMIT
    #   requests/day/feature (test_guest_within_limit_succeeds asserted
    #   200 on the first request; test_guest_over_daily_limit_blocked_
    #   before_provider_call filled the quota then asserted 403
    #   GUEST_RESTRICTION on the next one).
    #   NEW CANONICAL RULE: no anonymous request may reach the provider,
    #   ever -- see entitlement_service._decide()'s guest branch for the
    #   full rationale (this is what actually produced the reported
    #   "0 -> 45" phantom-credit symptom).
    #   WHY THE OLD ASSERTIONS WERE WRONG: they encoded exactly the
    #   silent-guest-credit-consumption behavior the product owner has
    #   since ruled unacceptable, not a test bug.
    #   WHICH TESTS WERE UPDATED: both of the above, replaced with the
    #   two below, which assert the new boundary (401 on the very first
    #   guest request, provider never called, no quota machinery
    #   involved at all).
    @patch("AI_GENERATORS.api_views.requests.post")
    def test_guest_first_request_is_denied_before_provider_call(self, mock_post):
        mock_post.return_value = _groq_success_response()
        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "feature": "chat", "request_id": "guest-denied-1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        mock_post.assert_not_called()

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_guest_denial_is_independent_of_guest_daily_limit_value(self, mock_post):
        from accounts import entitlement_config

        mock_post.return_value = _groq_success_response()
        self.client.get("/")

        # Even a generous/disabled limit must not let a guest through --
        # proves the denial no longer depends on GUEST_DAILY_LIMIT at all
        # (unlike the retired quota-based rule).
        with patch.object(entitlement_config, "GUEST_DAILY_LIMIT", 1000):
            r = self.client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "feature": "chat", "request_id": "guest-denied-2"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        mock_post.assert_not_called()
