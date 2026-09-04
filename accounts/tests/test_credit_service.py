import threading

from django.contrib.auth.models import User
from django.db import connections
from django.test import TestCase, TransactionTestCase

from accounts.models import CreditAccount, CreditTransaction
from accounts.services import credit_service
from accounts.services.exceptions import (
    CreditServiceError,
    DuplicateRequestError,
    InsufficientCreditsError,
    InvalidAmountError,
)

TT = CreditTransaction.TransactionType


class GetOrCreateAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")

    def test_creates_account_with_initial_grant(self):
        account = credit_service.get_or_create_account(user=self.user)
        self.assertGreater(account.balance, 0)
        self.assertEqual(
            CreditTransaction.objects.filter(credit_account=account, transaction_type=TT.GRANT).count(), 1
        )

    def test_idempotent_on_repeated_calls(self):
        acc1 = credit_service.get_or_create_account(user=self.user)
        acc2 = credit_service.get_or_create_account(user=self.user)
        self.assertEqual(acc1.pk, acc2.pk)
        # Only ONE initial grant, not one per call.
        self.assertEqual(
            CreditTransaction.objects.filter(credit_account=acc1, transaction_type=TT.GRANT).count(), 1
        )

    def test_guest_account_creation(self):
        account = credit_service.get_or_create_account(guest_key="sess-1")
        self.assertIsNone(account.user)
        self.assertGreater(account.balance, 0)

    def test_requires_exactly_one_identity(self):
        with self.assertRaises(CreditServiceError):
            credit_service.get_or_create_account()
        with self.assertRaises(CreditServiceError):
            credit_service.get_or_create_account(user=self.user, guest_key="sess-1")


class GrantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=0)

    def test_grant_increases_balance_and_lifetime_earned(self):
        credit_service.grant(self.account, amount=25, reference_id="grant-1")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, 25)
        self.assertEqual(self.account.lifetime_earned, 25)

    def test_grant_creates_ledger_transaction(self):
        txn = credit_service.grant(self.account, amount=25, reference_id="grant-2")
        self.assertEqual(txn.transaction_type, TT.GRANT)
        self.assertEqual(txn.amount, 25)
        self.assertEqual(txn.balance_before, 0)
        self.assertEqual(txn.balance_after, 25)

    def test_grant_rejects_non_positive_amount(self):
        with self.assertRaises(InvalidAmountError):
            credit_service.grant(self.account, amount=0, reference_id="bad")
        with self.assertRaises(InvalidAmountError):
            credit_service.grant(self.account, amount=-5, reference_id="bad2")

    def test_duplicate_grant_reference_id_rejected(self):
        credit_service.grant(self.account, amount=10, reference_id="dup-ref")
        with self.assertRaises(DuplicateRequestError):
            credit_service.grant(self.account, amount=10, reference_id="dup-ref")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, 10)  # not double-granted


class ReserveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=100)

    def test_successful_reservation(self):
        credit_service.reserve(self.account, amount=60, reference_id="req-1")
        self.account.refresh_from_db()
        self.assertEqual(self.account.reserved_balance, 60)
        self.assertEqual(self.account.balance, 100)
        self.assertEqual(self.account.available_balance, 40)

    def test_reservation_of_exact_balance(self):
        credit_service.reserve(self.account, amount=100, reference_id="req-exact")
        self.account.refresh_from_db()
        self.assertEqual(self.account.available_balance, 0)

    def test_insufficient_balance_rejected(self):
        with self.assertRaises(InsufficientCreditsError):
            credit_service.reserve(self.account, amount=101, reference_id="req-too-much")
        self.account.refresh_from_db()
        self.assertEqual(self.account.reserved_balance, 0)  # nothing reserved

    def test_second_reservation_exceeding_available_rejected(self):
        credit_service.reserve(self.account, amount=60, reference_id="req-a")
        with self.assertRaises(InsufficientCreditsError):
            credit_service.reserve(self.account, amount=60, reference_id="req-b")
        self.account.refresh_from_db()
        self.assertEqual(self.account.reserved_balance, 60)  # only the first stuck
        self.assertGreaterEqual(self.account.balance, self.account.reserved_balance)  # never negative available

    def test_duplicate_reference_id_rejected(self):
        credit_service.reserve(self.account, amount=10, reference_id="same-req")
        with self.assertRaises(DuplicateRequestError):
            credit_service.reserve(self.account, amount=10, reference_id="same-req")
        self.account.refresh_from_db()
        self.assertEqual(self.account.reserved_balance, 10)  # not double-reserved

    def test_reserve_requires_reference_id(self):
        with self.assertRaises(InvalidAmountError):
            credit_service.reserve(self.account, amount=10, reference_id="")


class FinalizeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=100)
        credit_service.reserve(self.account, amount=20, reference_id="req-1")
        self.account.refresh_from_db()

    def test_finalize_charges_actual_and_releases_rest_of_reservation(self):
        txn, charged = credit_service.finalize(
            self.account, reservation_amount=20, actual_amount=7, reference_id="req-1"
        )
        self.account.refresh_from_db()
        self.assertEqual(charged, 7)
        self.assertEqual(self.account.balance, 93)  # 100 - 7
        self.assertEqual(self.account.reserved_balance, 0)  # full 20 released
        self.assertEqual(self.account.lifetime_used, 7)
        self.assertEqual(txn.transaction_type, TT.USAGE)

    def test_finalize_never_charges_more_than_reserved(self):
        _txn, charged = credit_service.finalize(
            self.account, reservation_amount=20, actual_amount=999, reference_id="req-1"
        )
        self.assertEqual(charged, 20)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, 80)  # 100 - 20, not 100 - 999

    def test_duplicate_finalize_rejected(self):
        credit_service.finalize(self.account, reservation_amount=20, actual_amount=5, reference_id="req-1")
        with self.assertRaises(DuplicateRequestError):
            credit_service.finalize(self.account, reservation_amount=20, actual_amount=5, reference_id="req-1")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, 95)  # only charged once


class RefundTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=100)
        credit_service.reserve(self.account, amount=30, reference_id="req-1")
        self.account.refresh_from_db()

    def test_refund_restores_available_balance_without_charging(self):
        credit_service.refund(self.account, reservation_amount=30, reference_id="req-1")
        self.account.refresh_from_db()
        self.assertEqual(self.account.reserved_balance, 0)
        self.assertEqual(self.account.balance, 100)  # untouched
        self.assertEqual(self.account.available_balance, 100)

    def test_refund_creates_ledger_transaction(self):
        txn = credit_service.refund(self.account, reservation_amount=30, reference_id="req-1")
        self.assertEqual(txn.transaction_type, TT.REFUND)

    def test_duplicate_refund_rejected(self):
        credit_service.refund(self.account, reservation_amount=30, reference_id="req-1")
        with self.assertRaises(DuplicateRequestError):
            credit_service.refund(self.account, reservation_amount=30, reference_id="req-1")
        self.account.refresh_from_db()
        self.assertEqual(self.account.reserved_balance, 0)  # not double-refunded into negative


class MonthlyGrantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=0)

    def test_grants_once_per_period(self):
        txn1 = credit_service.grant_monthly_credits(self.account)
        self.assertIsNotNone(txn1)
        txn2 = credit_service.grant_monthly_credits(self.account)
        self.assertIsNone(txn2)  # already granted this period
        self.account.refresh_from_db()
        self.assertEqual(self.account.lifetime_earned, txn1.amount)


class ConcurrentReservationTests(TransactionTestCase):
    """
    Best-effort concurrency test. On Postgres, select_for_update() in
    credit_service.reserve() gives real row-level serialization and this
    test verifies the business outcome directly. On SQLite (this project's
    dev/test default), Django's select_for_update() is a documented no-op
    (see accounts/services/credit_service.py docstring) and SQLite's own
    file-level write locking is what actually serializes the two threads —
    which can surface as an OperationalError ("database is locked") from
    the losing thread rather than an InsufficientCreditsError. Both
    outcomes are treated as "correctly rejected" here; what the test
    asserts either way is the thing Step 4 actually requires: it is
    IMPOSSIBLE for both reservations to succeed and drive the account
    negative.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=100)

    def test_two_concurrent_reservations_never_both_succeed(self):
        results = {}

        def attempt(name, amount, ref):
            try:
                credit_service.reserve(self.account, amount=amount, reference_id=ref)
                results[name] = "success"
            except InsufficientCreditsError:
                results[name] = "rejected_insufficient"
            except Exception as exc:  # e.g. sqlite3.OperationalError: database is locked
                results[name] = f"rejected_other:{type(exc).__name__}"
            finally:
                connections.close_all()

        t1 = threading.Thread(target=attempt, args=("A", 60, "req-A"))
        t2 = threading.Thread(target=attempt, args=("B", 60, "req-B"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        successes = [name for name, outcome in results.items() if outcome == "success"]
        self.assertLessEqual(
            len(successes), 1, f"both reservations reported success: {results}"
        )

        self.account.refresh_from_db()
        self.assertGreaterEqual(self.account.balance, self.account.reserved_balance)
        self.assertGreaterEqual(self.account.available_balance, 0)
