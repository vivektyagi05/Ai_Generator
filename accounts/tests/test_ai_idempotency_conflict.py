"""
PHASE 1A Step 12 — idempotency: same request_id while a prior attempt is
still in flight (status RESERVED) must be refused with 409, not silently
processed a second time or double-reserved.

The successful-replay case (request_id already FINALIZED -> single charge,
single provider call) is covered by
accounts/tests/test_ai_credit_integration.py::
AICreditIntegrationTests.test_duplicate_request_id_does_not_double_charge --
not duplicated here.
"""

import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import AIUsage
from accounts.services import credit_service


@override_settings(GROQ_API_KEY="test-key")
class InFlightIdempotencyConflictTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="carl", password="x")
        self.client = Client()
        self.client.force_login(self.user)
        self.account = credit_service.get_or_create_account(user=self.user)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_same_request_id_while_processing_returns_conflict(self, mock_post):
        # Simulates a request that reserved credits and is (per this row's
        # status) still waiting on the provider -- e.g. a genuinely
        # concurrent duplicate submission, or a retry that raced ahead of
        # the first attempt's completion.
        AIUsage.objects.create(
            user=self.user,
            credit_account=self.account,
            request_id="in-flight-1",
            feature="chat",
            provider="groq",
            model="llama-3.1-8b-instant",
            status=AIUsage.Status.RESERVED,
            credits_reserved=2,
        )

        response = self.client.post(
            "/api/ai/",
            data=json.dumps({"prompt": "hi", "request_id": "in-flight-1"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        mock_post.assert_not_called()  # never re-attempted the provider call

        # Nothing about the reservation changed -- the original RESERVED
        # row is exactly as it was.
        usage = AIUsage.objects.get(request_id="in-flight-1")
        self.assertEqual(usage.status, AIUsage.Status.RESERVED)
        self.assertEqual(usage.credits_reserved, 2)


class GenuinelyConcurrentSameRequestIdTests(TestCase):
    """
    PHASE 8A -- Item 5 (credit race-condition audit). Covers the race
    window InFlightIdempotencyConflictTests above does NOT: two
    genuinely concurrent submissions of the same request_id where
    BOTH pass ai_usage_service.check_existing_usage()'s (unlocked,
    by design) SELECT before either has committed its reservation --
    unlike the case above, where the AIUsage row already exists in DB
    by the time the second request's check runs.

    Root cause (verified directly against the service layer, not
    assumed): the loser's credit_service.reserve() call inside
    begin_request() hits the DB-level uq_credittxn_account_reference_
    type constraint and raises DuplicateRequestError, which was
    previously uncaught anywhere in AI_GENERATORS/api_views.py:ai_generate()
    -- an unhandled exception, i.e. an unhandled 500 for the losing
    request, not any of the graceful, already-existing responses
    (_replay_existing()) every other duplicate-request-id path gets.

    Fix: ai_generate() now catches DuplicateRequestError, re-queries
    check_existing_usage() (guaranteed to now find the winner's
    committed row), and hands off to the exact same _replay_existing()
    every other duplicate uses -- no new response shape, no new file,
    reuses the existing idempotent-replay path.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="dave", password="x")
        self.client = Client()
        self.client.force_login(self.user)

    @override_settings(GROQ_API_KEY="test-key")
    @patch("AI_GENERATORS.api_views.requests.post")
    def test_losing_concurrent_request_gets_a_graceful_reply_not_a_500(self, mock_post):
        from accounts.services import ai_usage_service, credit_service

        # The "winner": reserves credits and creates the AIUsage row for
        # this request_id directly at the service layer, exactly as if
        # its own /api/ai/ request had already gotten past
        # check_existing_usage() and into begin_request() a moment
        # earlier -- it just hasn't reached the provider yet (still
        # RESERVED), matching a real race's timing.
        credit_service.get_or_create_account(user=self.user)
        ai_usage_service.begin_request(
            request_id="race-http-1",
            user=self.user,
            guest_key=None,
            feature="chat",
            provider="groq",
            model="llama-3.1-8b-instant",
            prompt="hi",
        )

        # The "loser": its own check_existing_usage() call is forced to
        # return None (as it genuinely would if it ran a moment before
        # the winner's row committed), so its /api/ai/ request proceeds
        # into begin_request() the same way the winner's did, and hits
        # the real DB-level constraint the winner already claimed.
        with patch("AI_GENERATORS.api_views.ai_usage_service.check_existing_usage", return_value=None):
            response = self.client.post(
                "/api/ai/",
                data=json.dumps({"prompt": "hi", "request_id": "race-http-1"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(
            response.json(), {"error": "This request is already being processed.", "request_id": "race-http-1"}
        )
        mock_post.assert_not_called()

        # Reservation state is exactly what the winner alone created --
        # the loser's failed attempt didn't double-reserve or corrupt it.
        usage = AIUsage.objects.get(request_id="race-http-1")
        self.assertEqual(usage.status, AIUsage.Status.RESERVED)
        account = credit_service.get_or_create_account(user=self.user)
        # Reserved balance reflects exactly one reservation, not two --
        # the loser's DuplicateRequestError never got far enough to
        # reserve a second time.
        self.assertEqual(account.reserved_balance, usage.credits_reserved)
