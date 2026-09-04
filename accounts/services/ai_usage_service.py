# accounts/services/ai_usage_service.py
"""
Ties AIUsage (the operational record of one AI request) to credit_service
(the generic ledger) so AI_GENERATORS/api_views.py can stay a thin HTTP
adapter around this lifecycle:

    check_existing_usage()      -- idempotency lookup
        |
    begin_request()             -- validate -> estimate cost -> reserve
        |                           (raises on validation/insufficient-credit
        |                            failure; nothing charged either way)
    [call the AI provider — stays in api_views.py]
        |
    complete_success()  or  complete_failure()
        (finalize: charge real usage,      (refund: release the full
         release the rest of the           reservation, nothing charged)
         reservation)

request_id is the idempotency key for the whole lifecycle: AIUsage.request_id
is a unique column, and reserve()/finalize()/refund() all pass it through as
`reference_id`, which is itself protected by the DB-level
uq_credittxn_account_reference_type constraint. So even if this module's
Python-level idempotency check (check_existing_usage) were bypassed by a
genuine race, the database still refuses to reserve/finalize/refund the
same request_id twice.
"""

from __future__ import annotations

from accounts import credit_config
from accounts.models import AIUsage
from accounts.services import credit_service
from accounts.services.exceptions import InsufficientCreditsError, InvalidAmountError


class ValidationFailure(Exception):
    """Raised by begin_request() when the incoming request itself is invalid
    (empty prompt, etc.) — this happens BEFORE any reservation, so nothing
    is ever charged or refunded for it."""


def check_existing_usage(request_id: str, *, user=None, guest_key: str | None = None) -> AIUsage | None:
    """
    Idempotency lookup. Only returns a record if it belongs to the SAME
    caller identity that's asking now — a guessed/reused request_id
    belonging to someone else is treated as not found, so replay can never
    leak another identity's AI output.
    """
    if not request_id:
        return None
    try:
        usage = AIUsage.objects.get(request_id=request_id)
    except AIUsage.DoesNotExist:
        return None

    if user is not None:
        if usage.user_id != getattr(user, "pk", None):
            return None
    else:
        if usage.guest_session_key != guest_key:
            return None

    return usage


def begin_request(
    *,
    request_id: str,
    user=None,
    guest_key: str | None = None,
    feature: str,
    provider: str,
    model: str,
    prompt: str,
) -> tuple[AIUsage, "credit_service.CreditAccount"]:
    """
    Validates the request, estimates its cost, and atomically reserves that
    many credits. On success, returns (ai_usage, credit_account) with
    ai_usage.status == RESERVED. Raises ValidationFailure or
    InsufficientCreditsError otherwise (after recording an AIUsage row for
    the failure — Step 1/11 auditability — with no reservation made).
    """
    if not prompt or not prompt.strip():
        AIUsage.objects.create(
            user=user,
            guest_session_key=guest_key or "",
            request_id=request_id,
            feature=feature,
            provider=provider,
            model=model,
            status=AIUsage.Status.VALIDATION_FAILURE,
            error_code="empty_prompt",
        )
        raise ValidationFailure("prompt must not be empty")

    account = credit_service.get_or_create_account(user=user, guest_key=guest_key)
    # PHASE 1 (generator architecture normalization): `feature` now flows
    # into the estimate so a flat-cost feature (currently just "photo" --
    # see accounts/credit_config.py FLAT_COST_FEATURES) is reserved at its
    # fixed cost instead of the token-based formula. No behavior change for
    # any existing token-billed feature.
    estimated_cost = credit_config.estimate_reservation_credits(prompt, feature=feature)

    try:
        credit_service.reserve(
            account,
            amount=estimated_cost,
            reference_id=request_id,
            description=f"Reservation for {feature} request",
            metadata={"feature": feature, "provider": provider, "model": model},
        )
    except InsufficientCreditsError:
        AIUsage.objects.create(
            user=user,
            guest_session_key=guest_key or "",
            credit_account=account,
            request_id=request_id,
            feature=feature,
            provider=provider,
            model=model,
            status=AIUsage.Status.INSUFFICIENT_CREDITS,
            error_code="insufficient_credits",
            credits_reserved=0,
            metadata={"estimated_cost": estimated_cost},
        )
        raise

    ai_usage = AIUsage.objects.create(
        user=user,
        guest_session_key=guest_key or "",
        credit_account=account,
        request_id=request_id,
        feature=feature,
        provider=provider,
        model=model,
        status=AIUsage.Status.RESERVED,
        credits_reserved=estimated_cost,
    )
    return ai_usage, account


def complete_success(
    ai_usage: AIUsage,
    account,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    result_preview: str = "",
    latency_ms: int | None = None,
) -> int:
    """
    Finalizes a successful provider call: computes the real charge from
    token usage (or falls back to the reservation if the provider didn't
    return usage — Step 8, never fabricated), finalizes the reservation,
    and marks the AIUsage row FINALIZED. Returns credits actually charged.
    """
    usage_unavailable = total_tokens is None

    if usage_unavailable:
        actual_cost = ai_usage.credits_reserved  # charge exactly what was reserved — no fabricated tokens
    else:
        actual_cost = credit_config.tokens_to_credits(total_tokens)

    _txn, charged = credit_service.finalize(
        account,
        reservation_amount=ai_usage.credits_reserved,
        actual_amount=actual_cost,
        reference_id=ai_usage.request_id,
        description=f"Usage charge for {ai_usage.feature} request",
        metadata={"total_tokens": total_tokens, "usage_unavailable": usage_unavailable},
    )

    ai_usage.status = AIUsage.Status.FINALIZED
    ai_usage.input_tokens = input_tokens
    ai_usage.output_tokens = output_tokens
    ai_usage.total_tokens = total_tokens
    ai_usage.usage_unavailable = usage_unavailable
    ai_usage.credits_charged = charged
    ai_usage.latency_ms = latency_ms
    # Small, bounded replay payload for idempotent re-delivery of the SAME
    # response on a duplicate request_id — not the full raw provider
    # response, just enough to answer identically without re-billing.
    ai_usage.metadata = {**ai_usage.metadata, "result_preview": result_preview[:4000]}
    ai_usage.save(
        update_fields=[
            "status", "input_tokens", "output_tokens", "total_tokens",
            "usage_unavailable", "credits_charged", "latency_ms", "metadata", "updated_at",
        ]
    )
    return charged


def complete_failure(
    ai_usage: AIUsage,
    account,
    *,
    status: str,
    error_code: str,
    latency_ms: int | None = None,
) -> None:
    """
    Releases the full reservation for a request that failed after it was
    reserved (provider error, timeout, malformed response, unexpected
    exception) — Step 11: provider failure after reservation always
    refunds, and credits are never left permanently reserved.
    """
    credit_service.refund(
        account,
        reservation_amount=ai_usage.credits_reserved,
        reference_id=ai_usage.request_id,
        description=f"Refund for failed {ai_usage.feature} request ({error_code})",
        metadata={"failure_status": status, "error_code": error_code},
    )

    ai_usage.status = AIUsage.Status.REFUNDED
    ai_usage.error_code = error_code
    ai_usage.credits_refunded = ai_usage.credits_reserved
    ai_usage.latency_ms = latency_ms
    ai_usage.metadata = {**ai_usage.metadata, "failure_status": status}
    ai_usage.save(
        update_fields=["status", "error_code", "credits_refunded", "latency_ms", "metadata", "updated_at"]
    )
