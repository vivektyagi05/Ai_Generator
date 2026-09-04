"""
PHASE 9 — Step 7 (account deletion / financial retention).

Business rule: financial and audit records must not be physically
CASCADE-deleted by self-service account deletion. Before this fix,
profile_delete() called user.delete() directly, which CASCADE-deleted
CreditTransaction, AIUsage, Subscription, SubscriptionAuditLog, and
Payment (real Razorpay payment records) along with the User row.

Fixed by accounts/services/account_deletion_service.py: anonymize +
deactivate the User row instead of deleting it, so every FK to it (and
therefore all financial/audit history) survives untouched. See that
module's docstring for the full design rationale.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    AIUsage,
    CreditTransaction,
    Payment,
    Plan,
    Subscription,
    SubscriptionAuditLog,
    UserProfile,
)
from accounts.services import account_deletion_service, credit_service

User = get_user_model()


def _make_user(username="deleteme@example.com"):
    return User.objects.create_user(username=username, email=username, password="pass12345")


class AccountDeletionFinancialRetentionTests(TestCase):
    """Deleting an account must not erase financial/audit history."""

    def setUp(self):
        self.user = _make_user()
        self.account = credit_service.get_or_create_account(user=self.user)
        credit_service.grant(
            self.account, amount=500, reference_id="grant-1", description="signup bonus"
        )

        self.plan = Plan.objects.get(code=Plan.Code.PREMIUM)
        self.subscription = Subscription.objects.create(
            user=self.user,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            billing_interval=Subscription.BillingInterval.MONTHLY,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )
        SubscriptionAuditLog.objects.create(
            subscription=self.subscription,
            event_type=SubscriptionAuditLog.EventType.ACTIVATED,
            source="test",
        )
        self.payment = Payment.objects.create(
            user=self.user,
            subscription=self.subscription,
            plan=self.plan,
            billing_interval=Subscription.BillingInterval.MONTHLY,
            provider="razorpay",
            status=Payment.Status.CAPTURED,
            amount=99900,
            currency="INR",
            order_id="order_test123",
        )
        AIUsage.objects.create(
            user=self.user,
            credit_account=self.account,
            request_id="req-retention-1",
            feature="code",
            provider="groq",
            model="test-model",
            status=AIUsage.Status.FINALIZED,
            credits_reserved=10,
            credits_charged=10,
        )

    def test_financial_and_audit_rows_survive_deletion(self):
        txn_count_before = CreditTransaction.objects.filter(user=self.user).count()
        self.assertGreater(txn_count_before, 0)

        account_deletion_service.delete_account(self.user)

        self.assertEqual(
            CreditTransaction.objects.filter(user=self.user).count(), txn_count_before
        )
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertTrue(Subscription.objects.filter(pk=self.subscription.pk).exists())
        self.assertTrue(
            SubscriptionAuditLog.objects.filter(subscription=self.subscription).exists()
        )
        self.assertTrue(AIUsage.objects.filter(request_id="req-retention-1").exists())

        # And they're still correctly attributed -- not orphaned/nulled.
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.user_id, self.user.pk)

    def test_preserved_records_remain_queryable_by_original_pk(self):
        user_id = self.user.pk
        account_deletion_service.delete_account(self.user)

        self.assertEqual(Payment.objects.filter(user_id=user_id).count(), 1)
        self.assertEqual(CreditTransaction.objects.filter(user_id=user_id).count() > 0, True)


class AccountDeletionAccessRevocationTests(TestCase):
    """Deleted/anonymized users cannot authenticate; active access is revoked."""

    def setUp(self):
        self.user = _make_user("revokeme@example.com")
        self.plan = Plan.objects.get(code=Plan.Code.PREMIUM)
        self.subscription = Subscription.objects.create(
            user=self.user,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            billing_interval=Subscription.BillingInterval.MONTHLY,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )

    def test_deleted_user_cannot_log_in_with_old_credentials(self):
        original_email = self.user.email
        account_deletion_service.delete_account(self.user)

        resp = self.client.post(
            "/login/", {"email": original_email, "password": "pass12345"}
        )
        self.assertEqual(resp.status_code, 200)  # re-rendered login page, not redirected in
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deleted_user_has_unusable_password_and_is_inactive(self):
        account_deletion_service.delete_account(self.user)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())

    def test_active_subscription_is_cancelled_on_deletion(self):
        account_deletion_service.delete_account(self.user)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.CANCELLED)

    def test_all_sessions_for_the_user_are_revoked(self):
        # Log in from two different "devices" (two separate clients).
        from django.test import Client

        client_a = Client()
        client_a.force_login(self.user)
        client_b = Client()
        client_b.force_login(self.user)

        self.assertTrue(
            Session.objects.filter(session_key=client_a.session.session_key).exists()
        )
        self.assertTrue(
            Session.objects.filter(session_key=client_b.session.session_key).exists()
        )

        account_deletion_service.delete_account(self.user)

        self.assertFalse(
            Session.objects.filter(session_key=client_a.session.session_key).exists()
        )
        self.assertFalse(
            Session.objects.filter(session_key=client_b.session.session_key).exists()
        )


class AccountDeletionIdempotencyAndIsolationTests(TestCase):
    def setUp(self):
        self.user = _make_user("idempotent@example.com")

    def test_deleting_twice_is_safe_and_does_not_re_anonymize(self):
        account_deletion_service.delete_account(self.user)
        self.user.refresh_from_db()
        first_username = self.user.username
        first_deleted_at = UserProfile.objects.get(user=self.user).deleted_at

        # Call again directly (bypassing the view's login_required, which
        # in practice already blocks this -- exercising the service's own
        # idempotency guarantee directly).
        account_deletion_service.delete_account(self.user)

        self.user.refresh_from_db()
        self.assertEqual(self.user.username, first_username)
        self.assertEqual(
            UserProfile.objects.get(user=self.user).deleted_at, first_deleted_at
        )

    def test_is_deleted_helper_reflects_state(self):
        self.assertFalse(account_deletion_service.is_deleted(self.user))
        account_deletion_service.delete_account(self.user)
        self.user.refresh_from_db()
        self.assertTrue(account_deletion_service.is_deleted(self.user))

    def test_deleting_one_account_does_not_affect_another_users_data(self):
        other = _make_user("untouched@example.com")
        other_account = credit_service.get_or_create_account(user=other)
        txn_count_before = CreditTransaction.objects.filter(user=other).count()
        credit_service.grant(
            other_account, amount=250, reference_id="other-grant", description="test"
        )

        account_deletion_service.delete_account(self.user)

        other.refresh_from_db()
        self.assertTrue(other.is_active)
        self.assertEqual(other.email, "untouched@example.com")
        self.assertEqual(
            CreditTransaction.objects.filter(user=other).count(), txn_count_before + 1
        )

    def test_anonymized_email_frees_original_email_for_new_signup(self):
        original_email = self.user.email
        account_deletion_service.delete_account(self.user)
        self.assertFalse(User.objects.filter(email=original_email).exists())
        self.assertFalse(User.objects.filter(username=original_email).exists())
