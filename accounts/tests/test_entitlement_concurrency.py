"""
PHASE 2 Step 26 — concurrency test for UserEntitlement / entitlement_admin.

Same gating rationale as accounts/tests/test_credit_concurrency.py: SQLite
does not meaningfully enforce select_for_update() row locking, so this test
is skipped unless running against PostgreSQL. See that file's docstring for
the full explanation (not repeated here) and PHASE_2_COMPLETION_AUDIT.md for
this project's current, honestly-disclosed verification status (NOT
VERIFIED in the sandbox this was built in -- no reachable Postgres
instance).
"""

import threading
import unittest

from django.contrib.auth.models import User
from django.db import connection
from django.test import TransactionTestCase

from accounts.models import Plan, UserEntitlement
from accounts.services import entitlement_admin


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "select_for_update() row-locking is only meaningfully enforced on "
    "PostgreSQL in this project (SQLite silently ignores it) -- see this "
    "file's module docstring and PHASE_2_ENTITLEMENT_ENGINE.md's "
    "concurrency section. Run against a Postgres DATABASES config to "
    "actually verify.",
)
class EntitlementGrantConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="concurrent_racer", password="x")
        # PHASE 3 fix: get_or_create rather than get(). TransactionTestCase
        # truncates all tables (including migration-seeded catalog rows)
        # after every test in the class unless serialized_rollback is set,
        # so a different TransactionTestCase earlier in the same run can
        # leave this table empty by the time this one starts -- self-seed
        # rather than depend on migration data surviving across unrelated
        # TransactionTestCases. (Bug found while adding Phase 3's own
        # TransactionTestCase-based concurrency tests -- see
        # PHASE_3_COMPLETION_AUDIT.md.)
        self.plan, _ = Plan.objects.get_or_create(
            code="PREMIUM", defaults={"name": "Premium", "display_order": 1}
        )

    def test_two_concurrent_grants_do_not_create_duplicate_active_rows(self):
        barrier = threading.Barrier(2)

        def attempt():
            try:
                barrier.wait(timeout=5)
                entitlement_admin.grant_entitlement(
                    user=self.user, plan=self.plan, performed_by=None, reason="concurrency test"
                )
            finally:
                connection.close()

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        active_count = UserEntitlement.objects.filter(
            user=self.user, plan=self.plan, status=UserEntitlement.Status.ACTIVE
        ).count()
        self.assertEqual(active_count, 1)
