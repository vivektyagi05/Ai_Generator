import json
from unittest.mock import Mock, patch

import requests as requests_lib
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import AIUsage, CreditAccount


def _groq_success_response(content="hello world", prompt_tokens=10, completion_tokens=5):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return resp


def _groq_error_response(status_code=500, error="boom"):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = {"error": error}
    resp.text = json.dumps({"error": error})
    return resp


@override_settings(GROQ_API_KEY="test-key")
class AICreditIntegrationTests(TestCase):
    def setUp(self):
        # The AI rate limiter uses Django's cache framework, which is not
        # reset between tests automatically (it isn't part of the DB
        # transaction TestCase rolls back). Clear it so counters from other
        # test files/classes don't bleed into this one's guest/IP limits.
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(username="ada", password="x")
        self.client.force_login(self.user)

    def _post(self, **body_overrides):
        body = {"prompt": "write a haiku"}
        body.update(body_overrides)
        return self.client.post("/api/ai/", data=json.dumps(body), content_type="application/json")

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_successful_request_charges_credits_and_returns_result(self, mock_post):
        mock_post.return_value = _groq_success_response()

        response = self._post(request_id="req-success-1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["result"], "hello world")
        self.assertGreater(data["credits_charged"], 0)

        usage = AIUsage.objects.get(request_id="req-success-1")
        self.assertEqual(usage.status, AIUsage.Status.FINALIZED)
        self.assertEqual(usage.total_tokens, 15)
        self.assertFalse(usage.usage_unavailable)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)  # nothing left reserved
        self.assertEqual(account.lifetime_used, usage.credits_charged)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_missing_usage_falls_back_to_reserved_amount_not_fabricated(self, mock_post):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "no usage field"}}]}
        mock_post.return_value = resp

        response = self._post(request_id="req-no-usage")
        self.assertEqual(response.status_code, 200)

        usage = AIUsage.objects.get(request_id="req-no-usage")
        self.assertTrue(usage.usage_unavailable)
        self.assertIsNone(usage.total_tokens)
        self.assertEqual(usage.credits_charged, usage.credits_reserved)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_provider_http_failure_refunds(self, mock_post):
        mock_post.return_value = _groq_error_response(500, "server exploded")

        response = self._post(request_id="req-fail-http")
        self.assertEqual(response.status_code, 502)

        usage = AIUsage.objects.get(request_id="req-fail-http")
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)
        self.assertEqual(usage.credits_refunded, usage.credits_reserved)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)  # nothing left dangling reserved

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_timeout_refunds(self, mock_post):
        mock_post.side_effect = requests_lib.exceptions.Timeout()

        response = self._post(request_id="req-timeout")
        self.assertEqual(response.status_code, 503)

        usage = AIUsage.objects.get(request_id="req-timeout")
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)
        self.assertEqual(usage.error_code, "provider_timeout")

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_network_failure_refunds(self, mock_post):
        mock_post.side_effect = requests_lib.exceptions.ConnectionError()

        response = self._post(request_id="req-network")
        self.assertEqual(response.status_code, 503)

        usage = AIUsage.objects.get(request_id="req-network")
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.reserved_balance, 0)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_malformed_response_refunds(self, mock_post):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"unexpected": "shape"}
        mock_post.return_value = resp

        response = self._post(request_id="req-malformed")
        self.assertEqual(response.status_code, 502)

        usage = AIUsage.objects.get(request_id="req-malformed")
        self.assertEqual(usage.status, AIUsage.Status.REFUNDED)
        self.assertEqual(usage.error_code, "provider_response_missing_content")

    def test_empty_prompt_validation_failure_does_not_charge(self):
        response = self._post(prompt="   ", request_id="req-empty")
        self.assertEqual(response.status_code, 400)

        usage = AIUsage.objects.get(request_id="req-empty")
        self.assertEqual(usage.status, AIUsage.Status.VALIDATION_FAILURE)
        self.assertIsNone(usage.credit_account)  # no account touched at all
        self.assertFalse(CreditAccount.objects.filter(user=self.user).exists())

    def test_insufficient_credits_blocks_request(self):
        # Pre-create the account with a balance too low to cover any
        # request, so get_or_create_account() finds it (created=False) and
        # does NOT hand out the initial free grant again.
        CreditAccount.objects.create(user=self.user, balance=0)

        response = self._post(request_id="req-broke")
        self.assertEqual(response.status_code, 402)

        usage = AIUsage.objects.get(request_id="req-broke")
        self.assertEqual(usage.status, AIUsage.Status.INSUFFICIENT_CREDITS)
        self.assertEqual(usage.credits_reserved, 0)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.balance, 0)
        self.assertEqual(account.reserved_balance, 0)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_duplicate_request_id_does_not_double_charge(self, mock_post):
        mock_post.return_value = _groq_success_response()

        first = self._post(request_id="req-dup")
        self.assertEqual(first.status_code, 200)
        first_charged = first.json()["credits_charged"]

        second = self._post(request_id="req-dup")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json().get("replayed"))

        # Provider was only actually called once.
        self.assertEqual(mock_post.call_count, 1)

        account = CreditAccount.objects.get(user=self.user)
        self.assertEqual(account.lifetime_used, first_charged)  # not charged twice

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_client_supplied_credits_field_is_ignored(self, mock_post):
        mock_post.return_value = _groq_success_response()

        response = self._post(request_id="req-spoof", credits=999999999, balance=999999999)
        self.assertEqual(response.status_code, 200)

        account = CreditAccount.objects.get(user=self.user)
        self.assertLess(account.balance, 999999999)


@override_settings(GROQ_API_KEY="test-key")
class AICreditGuestTests(TestCase):
    def setUp(self):
        cache.clear()

    # PHASE 8A CANONICAL RULE CHANGE:
    #   OLD RULE (this test's old name/body): a guest's very first request
    #   succeeded (200) by spending straight out of the lazily-created
    #   INITIAL_FREE_CREDITS guest grant, producing a real AIUsage row
    #   with no `user`.
    #   NEW CANONICAL RULE: a guest must never reach the provider or the
    #   credit ledger at all -- see entitlement_service._decide()'s guest
    #   branch. This is the exact mechanism behind the reported "0 -> 45"
    #   phantom-credit bug (the 50-credit grant + 5-credit charge were
    #   both real, just against an account nothing should have let a
    #   guest spend from).
    #   WHY THE OLD ASSERTION WAS WRONG: "guest can use free initial
    #   grant" was the bug, not a feature -- it let unauthenticated
    #   traffic consume real (Groq-billed) provider capacity.
    #   WHICH TEST WAS UPDATED: this one, renamed and re-pointed at the
    #   new boundary -- no AIUsage row, no CreditAccount, and the
    #   provider is never called for a guest.
    @patch("AI_GENERATORS.api_views.requests.post")
    def test_guest_cannot_reach_provider_or_ledger(self, mock_post):
        mock_post.return_value = _groq_success_response()
        client = Client()

        response = client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "guest-req-1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        mock_post.assert_not_called()
        self.assertFalse(AIUsage.objects.filter(request_id="guest-req-1").exists())
        self.assertFalse(CreditAccount.objects.exists())

    def test_two_different_guest_sessions_get_separate_accounts(self):
        client_a = Client()
        client_b = Client()

        # Force each client to establish its own server-side session
        # (mirrors what identify_requester() does on the first AI call).
        client_a.get("/credits/balance/")
        client_b.get("/credits/balance/")

        key_a = client_a.session.session_key
        key_b = client_b.session.session_key
        self.assertIsNotNone(key_a)
        self.assertIsNotNone(key_b)
        self.assertNotEqual(key_a, key_b)
