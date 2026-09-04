"""
PHASE 8 ARCHITECTURE RECOVERY -- Priority 2 (guest -> user credit
lifecycle).

Root cause of the reported "0 -> 50 -> 0" credit flicker: guest sessions
get their own CreditAccount (accounts/services/guest_identity.py +
credit_service.get_or_create_account(guest_key=...)), but nothing ever
moved that balance into the CreditAccount created for the user on
signup/login -- traced every call site in accounts/views.py and found no
guest->user migration existed at all. A guest who had spent down toward 0
would sign up and see a brand-new 50-credit grant (a different account),
which is consistent with the "0 -> 50" half of the report; a page/component
still reading against the old guest-scoped account/session would then show
0 again.

credit_service.migrate_guest_credits_to_user() + the accounts/views.py
user_login()/verify_otp() call sites fix this. These tests cover the
lettered scenarios from the Phase 8 directive (A-L) that are actually
testable at this layer -- I (AIUsage/ChatHistory ownership) is out of
scope for a credit-ledger test file and is called out as a documented,
not silently assumed, gap in migrate_guest_credits_to_user()'s docstring.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase

from accounts import credit_config
from accounts.models import CreditAccount, CreditTransaction
from accounts.services import credit_service


def _start_guest_session(client: Client) -> str:
    """Establishes a real, server-issued session key the same way an
    actual anonymous request would (guest_identity.get_or_create_guest_key
    just wraps this), so tests exercise the real code path rather than a
    shortcut that could drift from it."""
    client.get("/home/")
    session = client.session
    if not session.session_key:
        session.save()
    return session.session_key


class ScenarioA_GuestWithCreditsSignsUp(TestCase):
    def test_leftover_guest_balance_moves_to_new_user_account_no_double_grant(self):
        client = Client()
        guest_key = _start_guest_session(client)
        guest_account = credit_service.get_or_create_account(guest_key=guest_key)
        self.assertEqual(guest_account.balance, credit_config.INITIAL_FREE_CREDITS)

        # Guest spends some of it.
        credit_service.reserve(guest_account, amount=20, reference_id="req-1")
        credit_service.finalize(guest_account, reservation_amount=20, actual_amount=20, reference_id="req-1")
        guest_account.refresh_from_db()
        remaining = guest_account.available_balance
        self.assertEqual(remaining, credit_config.INITIAL_FREE_CREDITS - 20)

        user = User.objects.create_user(username="a@example.com", email="a@example.com", password="pw12345!")
        user_account = credit_service.migrate_guest_credits_to_user(guest_key, user)

        # Exactly the leftover guest balance -- NOT another 50-credit
        # initial grant on top of it.
        self.assertEqual(user_account.balance, remaining)
        self.assertEqual(user_account.available_balance, remaining)

        guest_account.refresh_from_db()
        self.assertEqual(guest_account.available_balance, 0)

        migration_txns = CreditTransaction.objects.filter(
            reference_id=f"guest-migration:{guest_key}"
        )
        self.assertEqual(migration_txns.count(), 2)
        amounts = sorted(t.amount for t in migration_txns)
        self.assertEqual(amounts, [-remaining, remaining])


class ScenarioB_GuestWithZeroCreditsSignsUp(TestCase):
    def test_no_phantom_credits_and_no_second_initial_grant(self):
        client = Client()
        guest_key = _start_guest_session(client)
        guest_account = credit_service.get_or_create_account(guest_key=guest_key)
        # Spend the entire initial grant.
        credit_service.reserve(guest_account, amount=credit_config.INITIAL_FREE_CREDITS, reference_id="spend-all")
        credit_service.finalize(
            guest_account,
            reservation_amount=credit_config.INITIAL_FREE_CREDITS,
            actual_amount=credit_config.INITIAL_FREE_CREDITS,
            reference_id="spend-all",
        )
        guest_account.refresh_from_db()
        self.assertEqual(guest_account.available_balance, 0)

        user = User.objects.create_user(username="b@example.com", email="b@example.com", password="pw12345!")
        user_account = credit_service.migrate_guest_credits_to_user(guest_key, user)

        # Zero, not a fresh 50 -- they already had their one-time grant
        # as a guest.
        self.assertEqual(user_account.balance, 0)
        self.assertFalse(
            CreditTransaction.objects.filter(
                credit_account=user_account, transaction_type=CreditTransaction.TransactionType.GRANT
            ).exists()
        )


class ScenarioC_GuestMigratesIntoAnAlreadyExistingUserAccount(TestCase):
    def test_migrates_into_existing_account_without_touching_its_balance_incorrectly(self):
        user = User.objects.create_user(username="c@example.com", email="c@example.com", password="pw12345!")
        existing_account = credit_service.get_or_create_account(user=user)
        self.assertEqual(existing_account.balance, credit_config.INITIAL_FREE_CREDITS)

        client = Client()
        guest_key = _start_guest_session(client)
        guest_account = credit_service.get_or_create_account(guest_key=guest_key)

        result_account = credit_service.migrate_guest_credits_to_user(guest_key, user)

        self.assertEqual(result_account.pk, existing_account.pk)
        self.assertEqual(
            result_account.balance,
            credit_config.INITIAL_FREE_CREDITS + credit_config.INITIAL_FREE_CREDITS,
        )


class ScenarioD_RepeatedMigrationAttemptIsNotDoubleCredited(TestCase):
    def test_calling_migration_twice_for_the_same_guest_key_only_credits_once(self):
        client = Client()
        guest_key = _start_guest_session(client)
        credit_service.get_or_create_account(guest_key=guest_key)

        user = User.objects.create_user(username="d@example.com", email="d@example.com", password="pw12345!")
        first = credit_service.migrate_guest_credits_to_user(guest_key, user)
        second = credit_service.migrate_guest_credits_to_user(guest_key, user)

        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.balance, credit_config.INITIAL_FREE_CREDITS)
        self.assertEqual(
            CreditTransaction.objects.filter(reference_id=f"guest-migration:{guest_key}").count(), 2
        )


class ScenarioNoGuestActivity(TestCase):
    def test_signup_with_no_prior_guest_ai_usage_still_gets_the_normal_initial_grant(self):
        client = Client()
        guest_key = _start_guest_session(client)
        # Deliberately never create a guest CreditAccount for this
        # session -- the common case (never touched a generator as a
        # guest before signing up).
        self.assertIsNone(CreditAccount.objects.filter(guest_session_key=guest_key).first())

        user = User.objects.create_user(username="e@example.com", email="e@example.com", password="pw12345!")
        user_account = credit_service.migrate_guest_credits_to_user(guest_key, user)
        self.assertEqual(user_account.balance, credit_config.INITIAL_FREE_CREDITS)

    def test_no_guest_key_at_all_still_gets_the_normal_initial_grant(self):
        user = User.objects.create_user(username="f@example.com", email="f@example.com", password="pw12345!")
        user_account = credit_service.migrate_guest_credits_to_user(None, user)
        self.assertEqual(user_account.balance, credit_config.INITIAL_FREE_CREDITS)


class InFlightReservationSurvivesMigrationTests(TestCase):
    def test_only_available_balance_migrates_reservation_stays_resolvable(self):
        client = Client()
        guest_key = _start_guest_session(client)
        guest_account = credit_service.get_or_create_account(guest_key=guest_key)
        # Reserve part of the balance -- simulates an in-flight generation
        # request at the moment the guest happens to submit the signup form.
        credit_service.reserve(guest_account, amount=10, reference_id="inflight-1")
        guest_account.refresh_from_db()
        available_before = guest_account.available_balance
        self.assertEqual(available_before, credit_config.INITIAL_FREE_CREDITS - 10)

        user = User.objects.create_user(username="g@example.com", email="g@example.com", password="pw12345!")
        user_account = credit_service.migrate_guest_credits_to_user(guest_key, user)
        self.assertEqual(user_account.balance, available_before)

        guest_account.refresh_from_db()
        self.assertEqual(guest_account.balance, guest_account.reserved_balance)  # exactly covers the reservation

        # The original reservation still finalizes cleanly against the
        # now-migrated guest account -- balance never goes negative, the
        # DB CheckConstraints (ck_creditaccount_balance_gte_reserved,
        # ck_creditaccount_balance_gte_0) are never violated.
        credit_service.finalize(guest_account, reservation_amount=10, actual_amount=10, reference_id="inflight-1")
        guest_account.refresh_from_db()
        self.assertEqual(guest_account.balance, 0)
        self.assertEqual(guest_account.reserved_balance, 0)


class EndToEndLoginFlowMigratesCreditsTests(TestCase):
    """Exercises the real user_login()/verify_otp() view code paths, not
    just the service function directly -- proves the guest_key really is
    captured before login()'s session-key rotation."""

    def test_real_login_view_migrates_guest_balance(self):
        client = Client()
        guest_key = _start_guest_session(client)
        guest_account = credit_service.get_or_create_account(guest_key=guest_key)
        credit_service.reserve(guest_account, amount=15, reference_id="spend-before-login")
        credit_service.finalize(
            guest_account, reservation_amount=15, actual_amount=15, reference_id="spend-before-login"
        )
        guest_account.refresh_from_db()
        remaining = guest_account.available_balance

        user = User.objects.create_user(username="h@example.com", email="h@example.com", password="pw12345!")
        resp = client.post("/login/", {"email": "h@example.com", "password": "pw12345!"})
        self.assertEqual(resp.status_code, 302)

        user_account = CreditAccount.objects.get(user=user)
        self.assertEqual(user_account.balance, remaining)
        self.assertNotEqual(user_account.balance, credit_config.INITIAL_FREE_CREDITS)  # not a second fresh grant

    def test_real_login_view_with_no_guest_history_still_grants_normally(self):
        client = Client()
        user = User.objects.create_user(username="i@example.com", email="i@example.com", password="pw12345!")
        resp = client.post("/login/", {"email": "i@example.com", "password": "pw12345!"})
        self.assertEqual(resp.status_code, 302)

        user_account = CreditAccount.objects.get(user=user)
        self.assertEqual(user_account.balance, credit_config.INITIAL_FREE_CREDITS)

    def test_final_frontend_balance_endpoint_agrees_with_the_migrated_server_balance(self):
        client = Client()
        guest_key = _start_guest_session(client)
        guest_account = credit_service.get_or_create_account(guest_key=guest_key)
        credit_service.reserve(guest_account, amount=30, reference_id="spend-before-login-2")
        credit_service.finalize(
            guest_account, reservation_amount=30, actual_amount=30, reference_id="spend-before-login-2"
        )
        guest_account.refresh_from_db()
        remaining = guest_account.available_balance

        user = User.objects.create_user(username="j@example.com", email="j@example.com", password="pw12345!")
        client.post("/login/", {"email": "j@example.com", "password": "pw12345!"})

        # /credits/balance/ is the same endpoint the nav pill's JS calls
        # (static/js/credits.js) -- proves the displayed balance is the
        # migrated one, not a stale/default value.
        resp = client.get("/credits/balance/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["available_balance"], remaining)
