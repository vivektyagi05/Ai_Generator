"""
PHASE 1A Step 7 — concurrency test for accounts/services/credit_service.py.

IMPORTANT (do not remove this gate): Django's SQLite backend does not
implement row-level locking for `SELECT ... FOR UPDATE` -- it is accepted
as valid syntax but has no serializing effect there. This project's default
DATABASES (AI_GENERATORS/settings.py) is SQLite, so running this test
against the default configuration would not actually exercise the
protection reserve() depends on, and a pass on SQLite would be a false
positive, not evidence of anything. This test is skipped unless the
configured database is PostgreSQL (`DATABASES["default"]["ENGINE"]` is the
postgres backend), and MUST be run against Postgres to be meaningful --
see PHASE_1_COMPLETION_AUDIT.md and PHASE_1_CREDIT_ENGINE.md's concurrency
section for exact commands and current verification status.

Uses TransactionTestCase (not TestCase): TestCase wraps each test in one
DB transaction shared by the whole test, which would prevent the two
threads below from ever seeing each other's uncommitted state -- exactly
the opposite of what a concurrency test needs. TransactionTestCase commits
for real, so each thread's transaction.atomic() block in credit_service is
genuinely independent, the way separate request-handling threads/processes
would be in production.
"""

import threading
import unittest

from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase

from accounts.models import CreditAccount
from accounts.services import credit_service
from accounts.services.exceptions import InsufficientCreditsError


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "select_for_update() row-locking is only meaningfully enforced on "
    "PostgreSQL in this project (SQLite silently ignores it) -- see this "
    "file's module docstring and PHASE_1_CREDIT_ENGINE.md's concurrency "
    "section. Run against a Postgres DATABASES config to actually verify.",
)
class ReserveConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="racer", password="x")
        self.account = CreditAccount.objects.create(user=self.user, balance=10)

    def test_two_concurrent_reserves_cannot_both_succeed_beyond_balance(self):
        results = []
        barrier = threading.Barrier(2)

        def attempt(reference_id):
            try:
                barrier.wait(timeout=5)
                credit_service.reserve(self.account, amount=8, reference_id=reference_id)
                results.append("ok")
            except InsufficientCreditsError:
                results.append("insufficient")
            finally:
                # Each thread needs its own connection lifecycle -- Django
                # DB connections are not thread-safe to share.
                connection.close()

        t1 = threading.Thread(target=attempt, args=("race-req-1",))
        t2 = threading.Thread(target=attempt, args=("race-req-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # balance=10, each reservation independently wants 8 -- both
        # succeeding would push reserved_balance to 16 > balance, which the
        # ck_creditaccount_balance_gte_reserved constraint (and the
        # select_for_update() serialization in reserve()) must prevent.
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("insufficient"), 1)

        self.account.refresh_from_db()
        self.assertLessEqual(self.account.reserved_balance, self.account.balance)
