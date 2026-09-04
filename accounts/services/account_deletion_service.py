# accounts/services/account_deletion_service.py
"""
PHASE 9 — Step 7: the single authoritative account-deletion flow.

Business rule (explicit, not invented): financial and audit records must
NOT be physically destroyed by self-service account deletion. Before this
module existed, accounts/views.py::profile_delete() called user.delete()
directly, which CASCADE-deleted CreditTransaction, AIUsage, Subscription,
SubscriptionAuditLog, and Payment (real Razorpay payment records) along
with the User row -- a user's own password was enough to permanently wipe
their entire real-money transaction history from the database.

Design: DON'T hard-delete the auth.User row at all. Every FK this project
has to User (CreditTransaction, AIUsage, Subscription, SubscriptionAuditLog,
Payment, ProviderEvent-via-Payment, UserEntitlement, EntitlementAuditLog)
stays exactly as it is -- on_delete=CASCADE is now moot because nothing
ever calls user.delete() for self-service deletion, and every existing
query/report/admin view that joins through user_id keeps working
unmodified. Instead:

  1. Cancel any live subscription (reuses subscription_service --
     Step 8/9's own rule: never duplicate grant/revoke/cancel logic).
  2. Revoke every active session for this user (explicit DB sweep --
     defense in depth on top of the automatic invalidation Django's
     session-auth-hash check already gives us once the password changes
     below, per Django's own AuthenticationMiddleware.get_user()).
  3. Scrub PII: delete the avatar file, clear phone/bio, anonymize
     username/email (both `deleted-user-<id>@deleted.invalid` -- this
     project's User.username IS the email; see accounts/views.py user_login
     authenticate(username=email, ...)), clear first/last name,
     set_unusable_password(), is_active=False.
  4. Mark UserProfile.deleted_at so this is detectable and idempotent.

What's deliberately NOT touched: CreditAccount.balance (an operational
number, not a document -- and moot anyway once login is impossible),
CreditTransaction/AIUsage/Subscription/SubscriptionAuditLog/Payment rows
(the financial/audit history this whole module exists to protect).

No PII beyond what already lived in these tables is retained "for the
record" -- this only stops the anonymization step itself from erasing the
financial ledger rows that reference the (now-anonymized) user_id; it
does not add new personal data to any of them.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from accounts.models import Subscription, UserProfile
from accounts.services import subscription_service

User = get_user_model()


def is_deleted(user) -> bool:
    """True if this account has already been through delete_account()."""
    profile = getattr(user, "userprofile", None)
    if profile is None:
        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            return False
    return profile.deleted_at is not None


def _revoke_all_sessions(user_id: int) -> None:
    """Delete every not-yet-expired Session row belonging to this user.

    Belt-and-suspenders alongside the password change below: Django's
    AuthenticationMiddleware already treats a session as invalid the
    moment the stored session-auth-hash stops matching the user's current
    password hash, and separately treats is_active=False as "no user" --
    so both changes made in this function already kill every session's
    *effect* immediately, on every request, everywhere, without this
    sweep. This sweep additionally removes the rows themselves, which
    matters for (a) not leaving stale rows keyed to an anonymized user
    lying around, and (b) making "revoked" an explicit, auditable action
    rather than an emergent property of two unrelated field changes.
    """
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        data = session.get_decoded()
        if str(data.get("_auth_user_id")) == str(user_id):
            session.delete()


@transaction.atomic
def delete_account(user) -> None:
    """
    Anonymize + deactivate `user`. Idempotent: calling this again on an
    already-deleted account is a safe no-op (does not re-anonymize, does
    not error, does not touch financial rows a second time).
    """
    profile, _ = UserProfile.objects.select_for_update().get_or_create(user=user)
    if profile.deleted_at is not None:
        return  # already deleted -- nothing further to do

    # 1. Cancel any live subscription. cancel_subscription() itself
    #    revokes the linked UserEntitlement and is idempotent, so a
    #    subscription that's already CANCELLED/EXPIRED is simply skipped
    #    by the LIVE_STATUSES filter below rather than re-processed.
    live_subscriptions = Subscription.objects.select_for_update().filter(
        user=user, status__in=Subscription.LIVE_STATUSES
    )
    for subscription in live_subscriptions:
        subscription_service.cancel_subscription(
            subscription=subscription,
            performed_by=None,
            source="account_deletion",
            reason="account deleted by user",
        )

    # 2. Revoke sessions before the account itself becomes unusable, so a
    #    concurrent request already in flight on another device is cut off
    #    as close to "now" as this transaction allows.
    _revoke_all_sessions(user.pk)

    # 3. Scrub PII (avatar file, phone, bio) — keep the row (don't
    #    CASCADE-delete UserProfile itself) so deleted_at has somewhere to
    #    live and so this function stays idempotent-detectable.
    if profile.avatar:
        profile.avatar.delete(save=False)
    profile.phone = ""
    profile.bio = ""
    profile.deleted_at = timezone.now()
    profile.save(update_fields=["avatar", "phone", "bio", "deleted_at"])

    # 4. Anonymize + deactivate the User row itself. username/email both
    #    get the anonymized value (this project's username IS the email --
    #    both fields must change or a stale one could still be looked up).
    #    Suffixing with the numeric pk keeps this collision-free even
    #    across many deleted accounts, and frees the original email for a
    #    fresh signup, matching ordinary self-service-deletion expectations.
    placeholder = f"deleted-user-{user.pk}@deleted.invalid"
    user.username = placeholder
    user.email = placeholder
    user.first_name = ""
    user.last_name = ""
    user.is_active = False
    user.set_unusable_password()
    user.save(
        update_fields=["username", "email", "first_name", "last_name", "is_active", "password"]
    )
