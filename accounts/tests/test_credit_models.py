from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import AIUsage, CreditAccount, CreditTransaction


class CreditAccountModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", email="ada@example.com", password="x")

    def test_account_starts_at_zero(self):
        account = CreditAccount.objects.create(user=self.user)
        self.assertEqual(account.balance, 0)
        self.assertEqual(account.reserved_balance, 0)
        self.assertEqual(account.available_balance, 0)
        self.assertEqual(account.lifetime_earned, 0)
        self.assertEqual(account.lifetime_used, 0)

    def test_one_account_per_user(self):
        CreditAccount.objects.create(user=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CreditAccount.objects.create(user=self.user)

    def test_guest_account_by_session_key(self):
        account = CreditAccount.objects.create(guest_session_key="sess-abc123")
        self.assertIsNone(account.user)
        self.assertEqual(account.guest_session_key, "sess-abc123")

    def test_account_requires_exactly_one_owner(self):
        # Neither user nor guest_session_key set.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CreditAccount.objects.create()

    def test_account_cannot_have_both_owners(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CreditAccount.objects.create(user=self.user, guest_session_key="sess-xyz")

    def test_balance_cannot_go_negative_at_db_level(self):
        account = CreditAccount.objects.create(user=self.user)
        account.balance = -1
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                account.save()

    def test_reserved_cannot_exceed_balance_at_db_level(self):
        account = CreditAccount.objects.create(user=self.user, balance=5)
        account.reserved_balance = 6
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                account.save()


class CreditTransactionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=10)

    def _make_txn(self, **overrides):
        defaults = dict(
            user=self.user,
            credit_account=self.account,
            transaction_type=CreditTransaction.TransactionType.GRANT,
            amount=10,
            balance_before=0,
            balance_after=10,
            reserved_before=0,
            reserved_after=0,
            reference_id="ref-1",
        )
        defaults.update(overrides)
        return CreditTransaction.objects.create(**defaults)

    def test_duplicate_reference_and_type_rejected(self):
        self._make_txn()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_txn()

    def test_same_reference_different_type_allowed(self):
        self._make_txn(transaction_type=CreditTransaction.TransactionType.RESERVE, reference_id="req-1")
        # Same reference_id, different type (USAGE) -- allowed, this is the
        # normal reserve -> finalize lifecycle for one request_id.
        self._make_txn(transaction_type=CreditTransaction.TransactionType.USAGE, reference_id="req-1")
        self.assertEqual(CreditTransaction.objects.filter(reference_id="req-1").count(), 2)

    def test_empty_reference_id_not_deduplicated(self):
        # The uniqueness constraint is conditioned on reference_id != "",
        # so multiple ADMIN_ADJUSTMENT rows with no reference_id are fine.
        self._make_txn(reference_id="", transaction_type=CreditTransaction.TransactionType.ADMIN_ADJUSTMENT)
        self._make_txn(reference_id="", transaction_type=CreditTransaction.TransactionType.ADMIN_ADJUSTMENT)
        self.assertEqual(
            CreditTransaction.objects.filter(transaction_type=CreditTransaction.TransactionType.ADMIN_ADJUSTMENT).count(),
            2,
        )


class AIUsageModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="x")

    def test_user_request_valid(self):
        usage = AIUsage.objects.create(
            user=self.user, request_id="r1", feature="chat", provider="groq", model="llama-3.1-8b-instant"
        )
        self.assertEqual(usage.status, AIUsage.Status.REQUESTED)

    def test_guest_request_valid(self):
        AIUsage.objects.create(
            guest_session_key="sess-1", request_id="r2", feature="chat", provider="groq", model="m"
        )

    def test_request_id_unique(self):
        AIUsage.objects.create(user=self.user, request_id="dup", provider="groq", model="m")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AIUsage.objects.create(user=self.user, request_id="dup", provider="groq", model="m")

    def test_neither_user_nor_guest_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AIUsage.objects.create(request_id="r3", provider="groq", model="m")
