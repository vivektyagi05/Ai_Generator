# accounts/services/subscription_service.py
"""
PHASE 3 — The single authoritative service for Subscription lifecycle.

Nothing outside this module is permitted to mutate Subscription.status,
current_period_start/end, cancel_at_period_end, cancelled_at, or ended_at
(Step 5: "Do NOT mutate subscription state directly from views" -- the same
rule Phase 2's entitlement_admin.py and Phase 1's credit_service.py already
follow for their own models).

This module owns exactly one integration responsibility: when a
Subscription becomes commercially ACTIVE, it drives the corresponding
UserEntitlement through accounts/services/entitlement_admin.py -- the
existing Phase 2 grant/revoke primitives are reused as-is, never
reimplemented here (Step 7/8: "Do not duplicate grant/revoke logic").

No payment provider is ever called from here. Every public function in this
module is safe to call from: (a) a future Phase 4 webhook handler, (b) an
admin action, or (c) a test fixture -- none of them are wired to a
client-facing "activate my subscription" endpoint (Step 18/25: no client
input may grant Premium).
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import billing_utils
from accounts.models import Subscription, SubscriptionAuditLog
from accounts.services import entitlement_admin
from accounts.services.exceptions import (
    DuplicateSubscriptionError,
    InvalidTransitionError,
)

Status = Subscription.Status

# ============================================================================
# State machine
# ============================================================================

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    Status.PENDING: frozenset({Status.ACTIVE, Status.CANCELLED}),
    Status.ACTIVE: frozenset({Status.PAST_DUE, Status.CANCELLED, Status.EXPIRED}),
    Status.PAST_DUE: frozenset({Status.ACTIVE, Status.GRACE_PERIOD, Status.EXPIRED}),
    Status.GRACE_PERIOD: frozenset({Status.ACTIVE, Status.EXPIRED}),
    Status.CANCELLED: frozenset({Status.EXPIRED}),
    Status.EXPIRED: frozenset(),  # terminal -- only a brand new Subscription can follow
}


def _assert_transition(subscription: Subscription, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(subscription.status, frozenset())
    if new_status not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition subscription {subscription.pk} from "
            f"{subscription.status} to {new_status}."
        )


def _log(subscription, event_type, *, performed_by=None, source="", reason="", metadata=None):
    SubscriptionAuditLog.objects.create(
        subscription=subscription,
        event_type=event_type,
        performed_by=performed_by,
        source=source,
        reason=reason,
        metadata=metadata or {},
    )


# ============================================================================
# Create
# ============================================================================


def create_subscription(
    *,
    user,
    plan,
    billing_interval: str,
    provider: str = "",
    provider_subscription_id: str | None = None,
    metadata: dict | None = None,
    performed_by=None,
    source: str = "subscription_service",
    reason: str = "subscription created",
) -> Subscription:
    """
    Creates a new PENDING subscription for `user`/`plan`. Does NOT activate
    it and does NOT touch UserEntitlement -- see activate_subscription()
    for that. Enforces "at most one live subscription per user"
    (uq_subscription_one_live_per_user) inside a locked transaction so two
    concurrent create calls can't both succeed (Step 14/23).
    """
    with transaction.atomic():
        existing = (
            Subscription.objects.select_for_update()
            .filter(user=user, status__in=Subscription.LIVE_STATUSES)
            .first()
        )
        if existing is not None:
            raise DuplicateSubscriptionError(
                f"User {user.pk} already has a live subscription (id={existing.pk}, "
                f"status={existing.status})."
            )

        # NOTE: select_for_update() above only locks rows that already
        # exist -- when no live subscription exists yet, it locks nothing,
        # so two concurrent create_subscription() calls can both pass the
        # `existing is None` check and both reach this INSERT. The actual
        # concurrency backstop for *creation* is therefore the DB-level
        # uq_subscription_one_live_per_user constraint, not this Python
        # check (Step 14/23: "Do not rely only on Python checks") -- caught
        # here and translated into the same typed exception so callers
        # don't need to know which layer caught it.
        try:
            subscription = Subscription.objects.create(
                user=user,
                plan=plan,
                status=Status.PENDING,
                billing_interval=billing_interval,
                provider=provider,
                provider_subscription_id=provider_subscription_id or None,
                metadata=metadata or {},
            )
        except IntegrityError as exc:
            raise DuplicateSubscriptionError(
                f"User {user.pk} already has a live subscription (race detected at the database level)."
            ) from exc
        _log(
            subscription,
            SubscriptionAuditLog.EventType.CREATED,
            performed_by=performed_by,
            source=source,
            reason=reason,
            metadata={"plan": plan.code, "billing_interval": billing_interval},
        )
        return subscription


# ============================================================================
# Activate
# ============================================================================


def activate_subscription(
    *,
    subscription: Subscription,
    performed_by=None,
    source: str = "subscription_service",
    reason: str = "subscription activated",
    provider_event_id: str = "",
    at=None,
) -> Subscription:
    """
    PENDING/PAST_DUE/GRACE_PERIOD -> ACTIVE. Sets the first (or restored)
    billing period and grants/extends the corresponding UserEntitlement via
    entitlement_admin.grant_entitlement (Step 8: reuse existing primitives).

    Idempotent: if this exact provider_event_id was already applied, or the
    subscription is already ACTIVE with a current period, this is a no-op
    that returns the subscription unchanged (Step 10).
    """
    at = at or timezone.now()
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)

        if provider_event_id and subscription.last_provider_event_id == provider_event_id:
            return subscription  # already applied -- idempotent replay

        if subscription.status == Status.ACTIVE and subscription.current_period_end:
            return subscription  # already active -- idempotent no-op

        _assert_transition(subscription, Status.ACTIVE)

        period_start = subscription.current_period_start or at
        period_end = billing_utils.compute_period_end(period_start, subscription.billing_interval)

        subscription.status = Status.ACTIVE
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end
        if provider_event_id:
            subscription.last_provider_event_id = provider_event_id
        subscription.save(
            update_fields=[
                "status",
                "current_period_start",
                "current_period_end",
                "last_provider_event_id",
                "updated_at",
            ]
        )

        entitlement = entitlement_admin.grant_entitlement(
            user=subscription.user,
            plan=subscription.plan,
            performed_by=performed_by,
            reason=reason,
            expires_at=period_end,
            source=f"subscription:{subscription.pk}",
        )
        if subscription.entitlement_id != entitlement.pk:
            subscription.entitlement = entitlement
            subscription.save(update_fields=["entitlement", "updated_at"])

        _log(
            subscription,
            SubscriptionAuditLog.EventType.ACTIVATED,
            performed_by=performed_by,
            source=source,
            reason=reason,
            metadata={"current_period_end": period_end.isoformat()},
        )
        return subscription


# ============================================================================
# Renew
# ============================================================================


def renew_subscription(
    *,
    subscription: Subscription,
    performed_by=None,
    source: str = "subscription_service",
    reason: str = "subscription renewed",
    provider_event_id: str = "",
) -> Subscription:
    """
    Advances the subscription to its next billing period and extends the
    entitlement expiry to match. Only valid from ACTIVE (a renewal that
    arrives while PAST_DUE/GRACE_PERIOD should go through
    restore_subscription/activate_subscription instead, since those
    represent "payment succeeded after a failure", not a routine renewal).

    Idempotent on provider_event_id and on an already-current period (Step
    9/10): replaying the same renewal twice never advances the period
    twice.
    """
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)

        if provider_event_id and subscription.last_provider_event_id == provider_event_id:
            return subscription

        if subscription.status != Status.ACTIVE:
            raise InvalidTransitionError(
                f"Cannot renew subscription {subscription.pk} from status {subscription.status}."
            )

        anchor = subscription.current_period_end or timezone.now()
        new_period_end = billing_utils.compute_period_end(anchor, subscription.billing_interval)

        subscription.current_period_start = anchor
        subscription.current_period_end = new_period_end
        if provider_event_id:
            subscription.last_provider_event_id = provider_event_id
        subscription.save(
            update_fields=[
                "current_period_start",
                "current_period_end",
                "last_provider_event_id",
                "updated_at",
            ]
        )

        if subscription.entitlement_id:
            entitlement_admin.grant_entitlement(
                user=subscription.user,
                plan=subscription.plan,
                performed_by=performed_by,
                reason=reason,
                expires_at=new_period_end,
                source=f"subscription:{subscription.pk}",
            )

        _log(
            subscription,
            SubscriptionAuditLog.EventType.RENEWED,
            performed_by=performed_by,
            source=source,
            reason=reason,
            metadata={"current_period_end": new_period_end.isoformat()},
        )
        return subscription


# ============================================================================
# Cancellation
# ============================================================================


def schedule_cancellation(
    *, subscription: Subscription, performed_by=None, source: str = "user", reason: str = "user requested cancellation"
) -> Subscription:
    """
    CANCEL_AT_PERIOD_END: the subscription stays commercially ACTIVE (and
    the entitlement stays granted) until current_period_end; expiration is
    handled uniformly by expire_subscription() when that time arrives
    (Step 11/12).
    """
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status != Status.ACTIVE:
            raise InvalidTransitionError(
                f"Cannot schedule cancellation for subscription {subscription.pk} "
                f"in status {subscription.status}."
            )
        if subscription.cancel_at_period_end:
            return subscription  # idempotent no-op

        subscription.cancel_at_period_end = True
        subscription.cancelled_at = timezone.now()
        subscription.save(update_fields=["cancel_at_period_end", "cancelled_at", "updated_at"])

        _log(
            subscription,
            SubscriptionAuditLog.EventType.CANCELLATION_SCHEDULED,
            performed_by=performed_by,
            source=source,
            reason=reason,
            metadata={"effective_at": subscription.current_period_end.isoformat()
                      if subscription.current_period_end else None},
        )
        return subscription


def restore_subscription(
    *, subscription: Subscription, performed_by=None, source: str = "user", reason: str = "user reversed cancellation"
) -> Subscription:
    """Reverses a scheduled (not-yet-effective) cancel_at_period_end. Does
    NOT resurrect an already-CANCELLED or EXPIRED subscription -- those are
    terminal; a new subscription is required (Step 6: no silent EXPIRED ->
    ACTIVE)."""
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status != Status.ACTIVE or not subscription.cancel_at_period_end:
            raise InvalidTransitionError(
                f"Subscription {subscription.pk} has no scheduled cancellation to restore."
            )
        subscription.cancel_at_period_end = False
        subscription.cancelled_at = None
        subscription.save(update_fields=["cancel_at_period_end", "cancelled_at", "updated_at"])

        _log(
            subscription,
            SubscriptionAuditLog.EventType.RESTORED,
            performed_by=performed_by,
            source=source,
            reason=reason,
        )
        return subscription


def cancel_subscription(
    *, subscription: Subscription, performed_by=None, source: str = "user", reason: str = "user requested immediate cancellation"
) -> Subscription:
    """
    CANCEL_NOW: ends the subscription immediately and revokes the
    entitlement immediately (Step 11). Idempotent: cancelling an already-
    CANCELLED subscription is a no-op.
    """
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status == Status.CANCELLED:
            return subscription
        _assert_transition(subscription, Status.CANCELLED)

        now = timezone.now()
        subscription.status = Status.CANCELLED
        subscription.ended_at = now
        subscription.save(update_fields=["status", "ended_at", "updated_at"])

        if subscription.entitlement_id and subscription.entitlement.status == "ACTIVE":
            entitlement_admin.revoke_entitlement(
                entitlement=subscription.entitlement, performed_by=performed_by, reason=reason
            )

        _log(
            subscription,
            SubscriptionAuditLog.EventType.CANCELLED,
            performed_by=performed_by,
            source=source,
            reason=reason,
        )
        return subscription


# ============================================================================
# Payment failure / grace period
# ============================================================================


def mark_past_due(
    *, subscription: Subscription, performed_by=None, source: str = "system:payment", reason: str = "payment failed"
) -> Subscription:
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status == Status.PAST_DUE:
            return subscription
        _assert_transition(subscription, Status.PAST_DUE)
        subscription.status = Status.PAST_DUE
        subscription.save(update_fields=["status", "updated_at"])
        _log(
            subscription,
            SubscriptionAuditLog.EventType.PAYMENT_FAILED,
            performed_by=performed_by,
            source=source,
            reason=reason,
        )
        return subscription


def enter_grace_period(
    *, subscription: Subscription, grace_days: int, performed_by=None, source: str = "system:payment", reason: str = "grace period started"
) -> Subscription:
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status == Status.GRACE_PERIOD:
            return subscription
        _assert_transition(subscription, Status.GRACE_PERIOD)

        deadline = billing_utils.grace_period_end(timezone.now(), grace_days)
        subscription.status = Status.GRACE_PERIOD
        subscription.metadata = {**subscription.metadata, "grace_period_ends_at": deadline.isoformat()}
        subscription.save(update_fields=["status", "metadata", "updated_at"])

        _log(
            subscription,
            SubscriptionAuditLog.EventType.GRACE_STARTED,
            performed_by=performed_by,
            source=source,
            reason=reason,
            metadata={"grace_period_ends_at": deadline.isoformat()},
        )
        return subscription


# ============================================================================
# Expiration
# ============================================================================


def expire_subscription(
    *, subscription: Subscription, performed_by=None, source: str = "system:expiration", reason: str = "subscription period ended"
) -> Subscription:
    """
    ACTIVE/PAST_DUE/GRACE_PERIOD/CANCELLED -> EXPIRED, and falls the
    entitlement back to FREE by revoking it (Step 12). Idempotent: expiring
    an already-EXPIRED subscription is a no-op.
    """
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
        if subscription.status == Status.EXPIRED:
            return subscription
        _assert_transition(subscription, Status.EXPIRED)

        subscription.status = Status.EXPIRED
        subscription.ended_at = timezone.now()
        subscription.save(update_fields=["status", "ended_at", "updated_at"])

        if subscription.entitlement_id and subscription.entitlement.status == "ACTIVE":
            entitlement_admin.revoke_entitlement(
                entitlement=subscription.entitlement, performed_by=performed_by, reason=reason
            )

        _log(
            subscription,
            SubscriptionAuditLog.EventType.EXPIRED,
            performed_by=performed_by,
            source=source,
            reason=reason,
        )
        return subscription


# ============================================================================
# Deterministic effective-state resolution (Step 12)
# ============================================================================


def get_effective_status(subscription: Subscription, at=None) -> str:
    """
    Derives what the subscription's status *should* be right now purely
    from timestamps, without mutating anything -- used so reads never
    depend solely on a cron job having already run (Step 12).
    """
    at = at or timezone.now()
    if subscription.status in Subscription.TERMINAL_STATUSES:
        return subscription.status
    if subscription.status == Status.GRACE_PERIOD:
        deadline_iso = (subscription.metadata or {}).get("grace_period_ends_at")
        if deadline_iso:
            deadline = timezone.datetime.fromisoformat(deadline_iso)
            if at >= deadline:
                return Status.EXPIRED
        return subscription.status
    if subscription.is_period_expired(at=at):
        return Status.EXPIRED
    return subscription.status


def sync_subscription(subscription: Subscription, performed_by=None) -> Subscription:
    """
    Reconciles a subscription's stored status with its deterministic
    effective status (Step 12) -- called lazily by the read API and safe to
    call repeatedly from a periodic background job once one exists. Never
    the ONLY mechanism relied upon: get_effective_status() above already
    lets a caller reason about state without needing this to have run.
    """
    effective = get_effective_status(subscription)
    if effective == Status.EXPIRED and subscription.status != Status.EXPIRED:
        return expire_subscription(
            subscription=subscription, performed_by=performed_by, source="system:expiration_sync"
        )
    return subscription


def get_lapsed_subscription_summary(user) -> dict | None:
    """
    PHASE 8B Rule 5/6 -- refund UX. Callers with no live Subscription (see
    Subscription.LIVE_STATUSES) fall back to reporting the user as plain
    FREE, which makes a refunded user and a user who was never Premium
    indistinguishable. This gives every such caller (profile_data,
    dashboard_view, ...) the same answer from one place instead of each
    re-deriving it -- originally written inline in profile_data (Phase 8B
    Item 2) and extracted here once a second caller (the dashboard) needed
    the identical logic, rather than copy-pasting it.

    Returns None when the user has never had a Subscription reach a
    terminal state (still live, or never subscribed at all). Otherwise
    returns {"plan", "ended_at", "reason"} for the most recently ended one,
    where reason is "refunded" or "expired". "Refunded" is recognized from
    the same SubscriptionAuditLog trail
    payment_service._handle_refund_processed already writes
    (source="system:razorpay_webhook", reason="payment refunded") -- not a
    second, divergent refunded flag on Subscription itself.
    """
    last_ended = (
        Subscription.objects.filter(user=user, status__in=Subscription.TERMINAL_STATUSES)
        .exclude(ended_at__isnull=True)
        .select_related("plan")
        .order_by("-ended_at")
        .first()
    )
    if last_ended is None:
        return None
    was_refunded = last_ended.audit_logs.filter(
        event_type="EXPIRED",
        source="system:razorpay_webhook",
        reason="payment refunded",
    ).exists()
    return {
        "plan": last_ended.plan.code,
        "ended_at": last_ended.ended_at.isoformat() if last_ended.ended_at else None,
        "reason": "refunded" if was_refunded else "expired",
    }
