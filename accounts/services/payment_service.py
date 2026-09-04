# accounts/services/payment_service.py
"""
PHASE 4 — The single authoritative service for Payment/ProviderEvent
lifecycle.

Nothing outside this module is permitted to mutate Payment.status,
amount, currency, order_id, payment_id, or ProviderEvent.status (same rule
subscription_service.py already enforces for Subscription -- see that
module's docstring). accounts/billing_views.py and accounts/webhook_views.py
are thin HTTP adapters around the functions here; they never touch these
models directly.

Every function that can lead to Premium access re-verifies, server-side,
that the payment amount/currency/order/user context matches what was
expected before calling into subscription_service (Step 17). The browser
is never trusted for payment success, amount, or status at any point in
this module (top-level STRICT REAL-MONEY RULE).

Architecture (Step 21, enforced by construction here -- no alternate path):

    Razorpay -> Payment -> subscription_service -> Subscription
             -> entitlement_admin (via subscription_service) -> UserEntitlement
"""

from __future__ import annotations

import hashlib
import logging

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import billing_config
from accounts.models import Payment, Plan, ProviderEvent, Subscription
from accounts.services import razorpay_client, subscription_service
from accounts.services.exceptions import (
    DuplicateLiveSubscriptionError,
    InvalidTransitionError,
    PaymentNotFoundError,
    PaymentVerificationError,
    UnpayablePlanError,
)

logger = logging.getLogger(__name__)

Status = Payment.Status


class _UnhandledEventType(Exception):
    """Internal sentinel: signature was valid but this event_type has no
    handler. Recorded as ProviderEvent.Status.IGNORED, never FAILED --
    an unrecognized-but-legitimate event is not an error."""


# ============================================================================
# Create order (checkout initiation)
# ============================================================================


def create_payment_order(
    *, user: User, plan_code: str, billing_interval: str
) -> tuple[Payment, dict]:
    """
    Resolves a server-side price for (plan_code, billing_interval),
    creates a local Payment(status=CREATED) tied to either a new PENDING
    Subscription (first-time purchase) or the user's existing live
    Subscription (renewal), then creates a matching Razorpay Order.

    Idempotent against double-click/browser retry (Step 9): if the user
    already has an in-flight CREATED Payment, that same row (and its
    already-created Razorpay order) is returned instead of minting a
    second order. Enforced at the DB level via
    uq_payment_one_created_per_user, not only by the Python check below
    (Step 9/23: concurrency).

    Returns (payment, checkout_info) where checkout_info contains ONLY
    values that are safe to send to the browser (Step 10).
    """
    if billing_interval not in Subscription.BillingInterval.values:
        raise UnpayablePlanError(f"Unknown billing_interval: {billing_interval!r}")
    if not billing_config.is_payable_plan(plan_code):
        raise UnpayablePlanError(f"Plan {plan_code!r} is not payable.")

    amount, currency = billing_config.resolve_price(plan_code, billing_interval)

    with transaction.atomic():
        existing_created = (
            Payment.objects.select_for_update()
            .filter(user=user, status=Status.CREATED)
            .first()
        )
        if existing_created is not None:
            if (
                existing_created.plan.code == plan_code
                and existing_created.billing_interval == billing_interval
                and existing_created.order_id
            ):
                # Same checkout the user already started -- reuse it rather
                # than creating a duplicate Razorpay order.
                return existing_created, _checkout_info(existing_created)
            raise DuplicateLiveSubscriptionError(
                "You already have a payment in progress for a different plan. "
                "Complete or wait for that checkout to resolve before starting another."
            )

        plan = Plan.objects.get(code=plan_code, is_active=True)

        live_subscription = (
            Subscription.objects.select_for_update()
            .filter(user=user, status__in=Subscription.LIVE_STATUSES)
            .first()
        )
        if live_subscription is None:
            # First-time purchase -- create a new PENDING subscription.
            # subscription_service enforces uq_subscription_one_live_per_user
            # itself, so no separate check is duplicated here.
            subscription = subscription_service.create_subscription(
                user=user,
                plan=plan,
                billing_interval=billing_interval,
                provider="razorpay",
                source="payment_service",
                reason="checkout started",
            )
        else:
            if live_subscription.plan_id != plan.pk or live_subscription.billing_interval != billing_interval:
                raise DuplicateLiveSubscriptionError(
                    "You already have a subscription; plan changes are not supported yet. "
                    "Cancel your current subscription before subscribing to a different plan."
                )
            subscription = live_subscription

        try:
            payment = Payment.objects.create(
                user=user,
                subscription=subscription,
                plan=plan,
                billing_interval=billing_interval,
                provider="razorpay",
                amount=amount,
                currency=currency,
                status=Status.CREATED,
            )
        except IntegrityError as exc:
            # Race: another concurrent request for this user won the
            # uq_payment_one_created_per_user constraint first.
            raise DuplicateLiveSubscriptionError(
                "A payment is already in progress (race detected at the database level)."
            ) from exc

        try:
            order = razorpay_client.create_order(
                amount=amount,
                currency=currency,
                receipt=f"payment_{payment.pk}",
                notes={"user_id": str(user.pk), "plan": plan_code, "billing_interval": billing_interval},
            )
        except Exception:
            # Local Payment row exists but no Razorpay order was created --
            # leave it CREATED with no order_id; the next create_payment_order
            # call for this user will retry the razorpay_client.create_order
            # call using the SAME local Payment row (idempotent retry point)
            # rather than creating a duplicate local record.
            logger.exception("Razorpay order creation failed", extra={"payment_id": payment.pk})
            raise

        payment.order_id = order["id"]
        payment.save(update_fields=["order_id", "updated_at"])

        return payment, _checkout_info(payment)


def _checkout_info(payment: Payment) -> dict:
    """Only values safe to hand to the browser -- no secrets, no internal
    ids beyond the Razorpay order id itself (Step 10)."""
    return {
        "key_id": razorpay_client.get_public_key_id(),
        "order_id": payment.order_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "plan": payment.plan.code,
        "billing_interval": payment.billing_interval,
    }


# ============================================================================
# Checkout callback verification (fast-path UX signal only -- NOT the
# activation trigger; see module docstring / PHASE_4_PAYMENT_ARCHITECTURE.md)
# ============================================================================


def verify_checkout_callback(
    *, user: User, order_id: str, payment_id: str, signature: str
) -> Payment:
    """
    Verifies the signature Razorpay's checkout.js reports back to the
    browser after a payment attempt, cross-checks the payment's real state
    via the Razorpay API, and (if everything matches) marks the local
    Payment AUTHORIZED.

    Deliberately does NOT call subscription_service here (Step 18/28 of the
    top-level brief: "The browser must NEVER be the source of truth for
    payment success" / "Never activate Premium from a redirect alone").
    Activation only ever happens from process_webhook_event's
    payment.captured handling below -- this function exists purely so the
    UI can show "payment received, confirming..." instead of a bare
    redirect, and so a forged/tampered client callback is provably
    rejected before it can even reach that stage.
    """
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(user=user, order_id=order_id)
        except Payment.DoesNotExist as exc:
            raise PaymentNotFoundError("No matching payment for this order.") from exc

        if payment.status in Payment.TERMINAL_STATUSES or payment.status == Status.CAPTURED:
            # Already resolved (possibly by the webhook, which may well
            # have arrived first) -- idempotent no-op, just return current
            # state rather than re-verifying or erroring.
            return payment

        # Step 11: cryptographic verification, never a bare string compare.
        razorpay_client.verify_checkout_signature(
            order_id=order_id, payment_id=payment_id, signature=signature
        )

        # Step 12: do not blindly trust the client-supplied payment_id
        # either -- fetch the real record from Razorpay and compare it
        # against what we expect for this order.
        remote = razorpay_client.fetch_payment(payment_id)
        if (
            remote.get("order_id") != payment.order_id
            or int(remote.get("amount", -1)) != payment.amount
            or remote.get("currency") != payment.currency
        ):
            logger.warning(
                "Razorpay checkout payment did not match expected order context",
                extra={"payment_id": payment.pk, "order_id": order_id},
            )
            raise PaymentVerificationError(
                "Payment details did not match the expected order."
            )

        payment.payment_id = payment_id
        payment.signature = signature
        payment.method = remote.get("method", "")[:30]
        if payment.status == Status.CREATED:
            payment.status = Status.AUTHORIZED
        payment.save(update_fields=["payment_id", "signature", "method", "status", "updated_at"])
        return payment


# ============================================================================
# Webhook processing (the actual source of truth for activation)
# ============================================================================


def _safe_event_summary(event_type: str, payload: dict) -> dict:
    """Extracts only non-sensitive, operationally useful fields from a
    webhook payload (Step 14: never store unnecessary secrets, never card/
    bank/VPA details)."""
    entity = (payload.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
    summary = {
        "event": event_type,
        "order_id": entity.get("order_id"),
        "payment_id": entity.get("id"),
        "amount": entity.get("amount"),
        "currency": entity.get("currency"),
        "status": entity.get("status"),
        "method": entity.get("method"),
    }
    if event_type.startswith("refund."):
        # Refund webhooks key the payment under payload.payment.entity (as
        # above, when Razorpay includes it) but the refund's own id/amount
        # live under payload.refund.entity -- record both non-sensitive
        # views rather than only the (possibly absent) payment one.
        refund_entity = (payload.get("payload", {}).get("refund", {}) or {}).get("entity", {}) or {}
        summary["refund_id"] = refund_entity.get("id")
        summary["refund_amount"] = refund_entity.get("amount")
        summary["refund_payment_id"] = refund_entity.get("payment_id")
        summary["refund_status"] = refund_entity.get("status")
    return summary


def process_webhook_event(*, event_id: str, event_type: str, raw_body: bytes, payload: dict) -> ProviderEvent:
    """
    Idempotently records and dispatches one verified Razorpay webhook
    delivery. The caller (webhook_views.py) MUST have already verified the
    request signature before calling this -- this function does not
    re-verify it.

    Idempotency (Step 15) is enforced primarily at the database level: the
    INSERT below either succeeds (this is the first time this event_id has
    been seen) or raises IntegrityError (a duplicate delivery, whether
    concurrent or a later retry) -- never a Python-only in-memory check.
    """
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    summary = _safe_event_summary(event_type, payload)

    try:
        with transaction.atomic():
            event = ProviderEvent.objects.create(
                provider="razorpay",
                event_id=event_id,
                event_type=event_type,
                payload_hash=payload_hash,
                payload_summary=summary,
                status=ProviderEvent.Status.RECEIVED,
            )
    except IntegrityError:
        logger.info("Duplicate Razorpay webhook delivery ignored", extra={"event_id": event_id})
        return ProviderEvent.objects.filter(provider="razorpay", event_id=event_id).first()

    try:
        _dispatch_event(event_id=event_id, event_type=event_type, payload=payload)
        event.status = ProviderEvent.Status.PROCESSED
    except _UnhandledEventType:
        event.status = ProviderEvent.Status.IGNORED
    except Exception as exc:  # noqa: BLE001 - must not let a handler bug crash webhook delivery
        logger.exception(
            "Error processing Razorpay webhook event",
            extra={"event_id": event_id, "event_type": event_type},
        )
        event.status = ProviderEvent.Status.FAILED
        event.processing_error = str(exc)[:500]

    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processing_error", "processed_at"])
    return event


def _dispatch_event(*, event_id: str, event_type: str, payload: dict) -> None:
    if event_type == "payment.captured":
        _handle_payment_captured(event_id=event_id, payload=payload)
    elif event_type == "payment.failed":
        _handle_payment_failed(event_id=event_id, payload=payload)
    elif event_type == "refund.processed":
        # PHASE 8B: closes the gap PHASE_4_COMPLETION_AUDIT.md documented
        # as "Refunds are modeled but not processed" -- Payment.Status.
        # REFUNDED existed in the state machine but no code path set it.
        _handle_refund_processed(event_id=event_id, payload=payload)
    else:
        # order.paid, refund.created, refund.failed, dispute.*, etc. --
        # still not handled (refund.created/refund.failed carry no
        # completed money movement to react to; dispute.* is genuinely
        # out of scope for this phase). Recorded IGNORED, not FAILED -- a
        # legitimate, simply-unhandled event type is not an error.
        raise _UnhandledEventType(event_type)


def _resolve_payment_for_webhook(entity: dict) -> Payment:
    order_id = entity.get("order_id")
    if not order_id:
        raise PaymentVerificationError("Webhook payload missing order_id.")
    try:
        # of=("self",) restricts the row lock to the Payment table itself.
        # Payment.subscription is nullable, so Django emits a LEFT OUTER
        # JOIN for select_related("subscription") -- PostgreSQL rejects
        # `FOR UPDATE` on the nullable side of an outer join outright
        # (NotSupportedError). We only ever need to lock Payment against
        # concurrent webhook/checkout-callback writers here, not
        # Subscription/Plan (those have their own locking inside
        # subscription_service's own select_for_update calls), so
        # excluding the joined tables from the lock is correct, not just
        # a workaround (Step 25/26: verified against real PostgreSQL).
        return (
            Payment.objects.select_for_update(of=("self",))
            .select_related("subscription", "plan")
            .get(provider="razorpay", order_id=order_id)
        )
    except Payment.DoesNotExist as exc:
        raise PaymentNotFoundError(f"No local Payment for Razorpay order_id={order_id!r}.") from exc


def _resolve_payment_for_refund_webhook(entity: dict) -> Payment:
    """Refund entities identify the payment by Razorpay payment_id, not
    order_id (a refund.processed payload's payload.refund.entity has no
    order_id field at all) -- a separate lookup from
    _resolve_payment_for_webhook above, not a variant of it."""
    payment_id = entity.get("payment_id")
    if not payment_id:
        raise PaymentVerificationError("Refund webhook payload missing payment_id.")
    try:
        return (
            Payment.objects.select_for_update(of=("self",))
            .select_related("subscription", "plan")
            .get(provider="razorpay", payment_id=payment_id)
        )
    except Payment.DoesNotExist as exc:
        raise PaymentNotFoundError(f"No local Payment for Razorpay payment_id={payment_id!r}.") from exc


def _handle_refund_processed(*, event_id: str, payload: dict) -> None:
    """
    PHASE 8B. Full refund (refunded amount == the payment's full captured
    amount): marks the Payment REFUNDED and, if this payment is still the
    one currently backing the subscription's access (no later renewal has
    already superseded it), immediately revokes access via
    expire_subscription -- a refund voids the purchase, so this uses
    expire (immediate) rather than cancel_subscription (which by design
    leaves access intact until the current period ends; that behavior is
    for a *voluntary* cancellation, not a reversed payment).

    Partial refund (refunded amount < the payment's amount): recorded in
    Payment.metadata for audit visibility only. No status or access change
    -- no prior phase of this project established a proration/partial-
    refund business rule, and inventing one here would be exactly what
    Step 20 prohibits ("do not manufacture business rules without
    evidence"). A human can act on the recorded metadata.
    """
    entity = (payload.get("payload", {}).get("refund", {}) or {}).get("entity", {}) or {}
    with transaction.atomic():
        # Deliberately not caught here -- PaymentVerificationError /
        # PaymentNotFoundError propagate to process_webhook_event's outer
        # handler, which records the delivery FAILED (operator-visible),
        # exactly mirroring _resolve_payment_for_webhook's identical
        # unknown-order_id case in _handle_payment_captured above.
        payment = _resolve_payment_for_refund_webhook(entity)

        if payment.provider_event_id == event_id or payment.status == Status.REFUNDED:
            return  # idempotent replay (Step 15)

        refund_amount = entity.get("amount")
        if refund_amount != payment.amount:
            logger.info(
                "Partial Razorpay refund recorded (no status/access change)",
                extra={
                    "payment_id": payment.pk,
                    "refund_amount": refund_amount,
                    "payment_amount": payment.amount,
                },
            )
            existing = list(payment.metadata.get("partial_refunds", []))
            existing.append({"refund_id": entity.get("id"), "amount": refund_amount, "event_id": event_id})
            payment.metadata = {**payment.metadata, "partial_refunds": existing}
            payment.provider_event_id = event_id
            payment.save(update_fields=["metadata", "provider_event_id", "updated_at"])
            return

        if payment.status != Status.CAPTURED:
            # A refund on a payment that was never CAPTURED locally (e.g.
            # FAILED, or still CREATED) is a data anomaly, not something
            # to act on -- mirrors _handle_payment_captured's identical
            # guard against terminal-status Payments above.
            logger.warning(
                "Received refund.processed for a non-CAPTURED Payment",
                extra={"payment_id": payment.pk, "status": payment.status},
            )
            return

        payment.status = Status.REFUNDED
        payment.provider_event_id = event_id
        payment.metadata = {**payment.metadata, "refund_id": entity.get("id")}
        payment.save(update_fields=["status", "metadata", "provider_event_id", "updated_at"])

        subscription = payment.subscription
        if subscription is None:
            return

        newer_capture_exists = (
            payment.captured_at is not None
            and Payment.objects.filter(subscription=subscription, status=Status.CAPTURED)
            .exclude(pk=payment.pk)
            .filter(captured_at__gt=payment.captured_at)
            .exists()
        )
        if newer_capture_exists:
            # A later renewal already superseded this payment before the
            # refund arrived -- current access is backed by that newer
            # capture, not this one, so it must not be revoked.
            logger.info(
                "Refunded payment superseded by a later renewal -- subscription left untouched",
                extra={"payment_id": payment.pk, "subscription_id": subscription.pk},
            )
            return

        if subscription.status not in (
            Subscription.Status.ACTIVE,
            Subscription.Status.PAST_DUE,
            Subscription.Status.GRACE_PERIOD,
            Subscription.Status.CANCELLED,
        ):
            return  # PENDING/already-EXPIRED: nothing live to revoke

        try:
            subscription_service.expire_subscription(
                subscription=subscription,
                source="system:razorpay_webhook",
                reason="payment refunded",
            )
        except InvalidTransitionError:
            logger.exception(
                "Refunded payment could not expire subscription (invalid transition)",
                extra={"payment_id": payment.pk, "subscription_id": subscription.pk},
            )
            raise


def _handle_payment_captured(*, event_id: str, payload: dict) -> None:
    entity = (payload.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
    with transaction.atomic():
        payment = _resolve_payment_for_webhook(entity)

        if payment.provider_event_id == event_id or payment.status == Status.CAPTURED:
            return  # already applied -- idempotent replay (Step 15)

        if payment.status in Payment.TERMINAL_STATUSES:
            # A FAILED/REFUNDED payment receiving a late captured event is
            # a data anomaly, not something to silently activate from.
            logger.warning(
                "Received payment.captured for a terminal-status Payment",
                extra={"payment_id": payment.pk, "status": payment.status},
            )
            return

        # Step 17: server-side amount/currency/order/user verification
        # before any activation -- never trust the webhook payload's
        # amount/currency at face value without comparing it against what
        # this specific order was created for.
        remote_amount = entity.get("amount")
        remote_currency = entity.get("currency")
        expected_amount, expected_currency = billing_config.resolve_price(
            payment.plan.code, payment.billing_interval
        )
        if (
            remote_amount != payment.amount
            or remote_amount != expected_amount
            or remote_currency != payment.currency
            or remote_currency != expected_currency
            or entity.get("order_id") != payment.order_id
        ):
            logger.error(
                "Razorpay payment.captured amount/currency/order mismatch -- refusing to activate",
                extra={"payment_id": payment.pk, "order_id": payment.order_id},
            )
            payment.status = Status.FAILED
            payment.failure_reason = "server-side amount/currency/order verification failed"
            payment.provider_event_id = event_id
            payment.save(
                update_fields=["status", "failure_reason", "provider_event_id", "updated_at"]
            )
            return

        payment.status = Status.CAPTURED
        payment.payment_id = entity.get("id", payment.payment_id)
        payment.method = (entity.get("method") or "")[:30]
        payment.captured_at = timezone.now()
        payment.provider_event_id = event_id
        payment.save(
            update_fields=[
                "status", "payment_id", "method", "captured_at", "provider_event_id", "updated_at",
            ]
        )

        subscription = payment.subscription
        if subscription is None:
            logger.error("Captured Payment has no linked subscription", extra={"payment_id": payment.pk})
            return

        try:
            if subscription.status == Subscription.Status.ACTIVE:
                subscription_service.renew_subscription(
                    subscription=subscription,
                    source="system:razorpay_webhook",
                    reason="payment captured",
                    provider_event_id=event_id,
                )
            else:
                # PENDING (first purchase) or PAST_DUE/GRACE_PERIOD
                # (recovering from a previous failed renewal) all funnel
                # through the same activate_subscription transition --
                # VALID_TRANSITIONS in subscription_service permits all
                # three -> ACTIVE.
                subscription_service.activate_subscription(
                    subscription=subscription,
                    source="system:razorpay_webhook",
                    reason="payment captured",
                    provider_event_id=event_id,
                )
        except InvalidTransitionError:
            logger.exception(
                "Captured payment could not activate subscription (invalid transition)",
                extra={"payment_id": payment.pk, "subscription_id": subscription.pk},
            )
            raise


def _handle_payment_failed(*, event_id: str, payload: dict) -> None:
    entity = (payload.get("payload", {}).get("payment", {}) or {}).get("entity", {}) or {}
    with transaction.atomic():
        payment = _resolve_payment_for_webhook(entity)

        if payment.provider_event_id == event_id or payment.status in Payment.TERMINAL_STATUSES:
            return  # already applied / already terminal -- idempotent no-op

        payment.status = Status.FAILED
        payment.payment_id = entity.get("id", payment.payment_id)
        payment.failure_reason = (entity.get("error_description") or "payment failed")[:500]
        payment.provider_event_id = event_id
        payment.save(
            update_fields=["status", "payment_id", "failure_reason", "provider_event_id", "updated_at"]
        )

        subscription = payment.subscription
        if subscription is None:
            return

        if subscription.status == Subscription.Status.PENDING:
            # First payment ever failed -- nothing to fall back to; cancel
            # the never-activated subscription so the user gets a clean
            # slate to retry with a brand new checkout (Step 19: failure
            # must not grant Premium, must not leave a stuck PENDING row
            # blocking uq_subscription_one_live_per_user).
            subscription_service.cancel_subscription(
                subscription=subscription,
                source="system:razorpay_webhook",
                reason="first payment failed",
            )
        elif subscription.status == Subscription.Status.ACTIVE:
            # A renewal payment failed on an otherwise-active subscription
            # -- existing Phase 3 primitive, not reinvented here (Step 19).
            subscription_service.mark_past_due(
                subscription=subscription,
                source="system:razorpay_webhook",
                reason="renewal payment failed",
            )
        # PAST_DUE/GRACE_PERIOD retry failing again: already in the
        # correct degraded state: no further action required here.
