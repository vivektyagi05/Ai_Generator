import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import CreditAccount
from accounts.services import credit_service
from accounts.services.exceptions import InvalidAmountError


class CrossUserIsolationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user_a = User.objects.create_user(username="alice", password="x")
        self.user_b = User.objects.create_user(username="bob", password="x")

    def test_balance_endpoint_only_ever_returns_the_caller_s_own_account(self):
        account_a = credit_service.get_or_create_account(user=self.user_a)
        credit_service.grant(account_a, amount=500, reference_id="bonus-for-alice")

        client_a = Client()
        client_a.force_login(self.user_a)
        resp_a = client_a.get("/credits/balance/")
        balance_a = resp_a.json()["balance"]

        client_b = Client()
        client_b.force_login(self.user_b)
        resp_b = client_b.get("/credits/balance/")
        balance_b = resp_b.json()["balance"]

        # Bob's balance must be his own (the small initial grant), never
        # Alice's 500-credit bonus, no matter what Bob's client sends.
        self.assertNotEqual(balance_b, balance_a)
        self.assertLess(balance_b, balance_a)

    def test_no_request_parameter_lets_a_user_target_another_users_account(self):
        account_a = credit_service.get_or_create_account(user=self.user_a)
        # Give Alice's account a distinct balance so a leak would be
        # unambiguous -- comparing against Alice's balance is meaningless
        # if both accounts happen to sit at the same default initial grant.
        credit_service.grant(account_a, amount=500, reference_id="bonus-for-alice")
        client_b = Client()
        client_b.force_login(self.user_b)

        # Bob tries every plausible field name to point the balance lookup
        # at Alice -- the view has no such parameter, identity comes only
        # from the authenticated session.
        for spoof_body in (
            {"user": self.user_a.username},
            {"user_id": self.user_a.pk},
            {"account_id": self.user_a.pk},
        ):
            resp = client_b.post(
                "/credits/balance/", data=json.dumps(spoof_body), content_type="application/json"
            )
            # credit_balance() doesn't even read the body, so this always
            # resolves to Bob's own account regardless of status code.
            if resp.status_code == 200:
                bob_account = CreditAccount.objects.get(user=self.user_b)
                self.assertEqual(
                    resp.json()["balance"],
                    credit_service.get_balance_snapshot(bob_account)["balance"],
                )
                self.assertNotEqual(resp.json()["balance"], credit_service.get_balance_snapshot(
                    CreditAccount.objects.get(user=self.user_a)
                )["balance"])


@override_settings(GROQ_API_KEY="test-key")
class NoClientControlledAmountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="ada", password="x")
        self.client = Client()
        self.client.force_login(self.user)

    @patch("AI_GENERATORS.api_views.requests.post")
    def test_ai_endpoint_never_reads_a_credit_amount_from_the_request_body(self, mock_post):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        mock_post.return_value = resp

        response = self.client.post(
            "/api/ai/",
            data=json.dumps({
                "prompt": "hi",
                "request_id": "req-1",
                # Every plausible client-side attempt to set the charge/balance directly:
                "credits": 0,
                "credits_charged": 0,
                "credit_delta": 1000000,
                "balance": 1000000,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        account = CreditAccount.objects.get(user=self.user)
        # Charged some small, real, token-derived amount -- not the
        # attacker-supplied 0, and balance is nowhere near the spoofed
        # 1,000,000.
        self.assertGreater(response.json()["credits_charged"], 0)
        self.assertLess(account.balance, 1000000)


class InvalidAmountRejectedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=100)

    def test_reserve_rejects_negative_amount(self):
        with self.assertRaises(InvalidAmountError):
            credit_service.reserve(self.account, amount=-10, reference_id="neg-1")

    def test_reserve_rejects_zero_amount(self):
        with self.assertRaises(InvalidAmountError):
            credit_service.reserve(self.account, amount=0, reference_id="zero-1")

    def test_finalize_rejects_negative_actual_amount(self):
        credit_service.reserve(self.account, amount=10, reference_id="req-x")
        with self.assertRaises(InvalidAmountError):
            credit_service.finalize(self.account, reservation_amount=10, actual_amount=-1, reference_id="req-x")
