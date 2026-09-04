# accounts/services/entitlement_admin.py
"""
PHASE 2 Step 19 — Controlled administrative entitlement overrides.

This is the ONLY code path allowed to create/modify a UserEntitlement
outside of automated test fixtures. It is not exposed by any URL — it is
called from accounts/admin.py (Django admin, staff-only + authenticated +
already audited by Django's own admin action log) via the actions below.
There is deliberately no client-facing "activate premium" endpoint (Step 8:
no such endpoint may exist).

Every call writes an EntitlementAuditLog row. Nothing here lets a caller
skip that -- these functions are the only way to mutate UserEntitlement,
mirroring how accounts/services/credit_service.py is the only code allowed
to touch CreditAccount.balance.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from accounts.models import EntitlementAuditLog, Plan, UserEntitlement


def grant_entitlement(
    *, user, plan: Plan, performed_by, reason: str, expires_at=None, source: str = "admin_override"
) -> UserEntitlement:
    """
    Grants `plan` to `user`, starting now. If the user already holds an
    ACTIVE entitlement for this plan, extends/replaces its expiry instead
    of creating a duplicate row (the DB uniqueness constraint would reject
    a second ACTIVE row for the same (user, plan) anyway -- this makes that
    the intended behavior rather than a caught IntegrityError).
    """
    if not reason:
        raise ValueError("reason is required for every entitlement override.")

    with transaction.atomic():
        existing = (
            UserEntitlement.objects.select_for_update()
            .filter(user=user, plan=plan, status=UserEntitlement.Status.ACTIVE)
            .first()
        )
        if existing is not None:
            existing.expires_at = expires_at
            existing.save(update_fields=["expires_at", "updated_at"])
            EntitlementAuditLog.objects.create(
                entitlement=existing,
                action=EntitlementAuditLog.Action.EXTEND,
                performed_by=performed_by,
                reason=reason,
                metadata={"expires_at": expires_at.isoformat() if expires_at else None},
            )
            return existing

        entitlement = UserEntitlement.objects.create(
            user=user,
            plan=plan,
            status=UserEntitlement.Status.ACTIVE,
            starts_at=timezone.now(),
            expires_at=expires_at,
            source=source,
        )
        EntitlementAuditLog.objects.create(
            entitlement=entitlement,
            action=EntitlementAuditLog.Action.GRANT,
            performed_by=performed_by,
            reason=reason,
            metadata={"plan": plan.code, "expires_at": expires_at.isoformat() if expires_at else None},
        )
        return entitlement


def revoke_entitlement(*, entitlement: UserEntitlement, performed_by, reason: str) -> UserEntitlement:
    if not reason:
        raise ValueError("reason is required for every entitlement override.")
    with transaction.atomic():
        entitlement.status = UserEntitlement.Status.INACTIVE
        entitlement.save(update_fields=["status", "updated_at"])
        EntitlementAuditLog.objects.create(
            entitlement=entitlement,
            action=EntitlementAuditLog.Action.REVOKE,
            performed_by=performed_by,
            reason=reason,
        )
    return entitlement


def suspend_entitlement(*, entitlement: UserEntitlement, performed_by, reason: str) -> UserEntitlement:
    if not reason:
        raise ValueError("reason is required for every entitlement override.")
    with transaction.atomic():
        entitlement.status = UserEntitlement.Status.SUSPENDED
        entitlement.save(update_fields=["status", "updated_at"])
        EntitlementAuditLog.objects.create(
            entitlement=entitlement,
            action=EntitlementAuditLog.Action.SUSPEND,
            performed_by=performed_by,
            reason=reason,
        )
    return entitlement


def reactivate_entitlement(*, entitlement: UserEntitlement, performed_by, reason: str) -> UserEntitlement:
    if not reason:
        raise ValueError("reason is required for every entitlement override.")
    with transaction.atomic():
        entitlement.status = UserEntitlement.Status.ACTIVE
        entitlement.save(update_fields=["status", "updated_at"])
        EntitlementAuditLog.objects.create(
            entitlement=entitlement,
            action=EntitlementAuditLog.Action.REACTIVATE,
            performed_by=performed_by,
            reason=reason,
        )
    return entitlement
