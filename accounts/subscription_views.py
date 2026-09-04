# accounts/subscription_views.py
"""
PHASE 3 Step 18/19 — subscription APIs for authenticated users.

Identity always comes from the authenticated session (request.user) --
never a user_id/subscription_id supplied by the client (Step 19/25: no
IDOR). None of these endpoints accept plan/status/provider/expires_at from
the client body; every state change goes through
accounts/services/subscription_service.py, which is the only code allowed
to mutate Subscription (Step 18: "There must be no client-controlled ...
that grants access").

Deliberately absent: POST /api/subscription/activate/. No such endpoint
exists anywhere in this project -- a user cannot become Premium by calling
an API (Step 18/25). Activation is only ever driven by
subscription_service.activate_subscription(), called from an admin action
or (in Phase 4) a verified payment webhook.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Subscription
from accounts.services import entitlement_service, subscription_service
from accounts.services.exceptions import InvalidTransitionError


def _get_live_subscription(user):
    """The user's current live (non-terminal) subscription, if any. Never
    returns another user's row -- always filtered by the authenticated
    identity (Step 19)."""
    return (
        Subscription.objects.filter(user=user, status__in=Subscription.LIVE_STATUSES)
        .select_related("plan")
        .first()
    )


def _serialize(subscription: Subscription | None, *, fallback_plan: str) -> dict:
    if subscription is None:
        return {
            "plan": fallback_plan,
            "status": "ACTIVE",
            "billing_interval": None,
            "current_period_start": None,
            "current_period_end": None,
            "cancel_at_period_end": False,
        }
    return {
        "plan": subscription.plan.code,
        "status": subscription.status,
        "billing_interval": subscription.billing_interval,
        "current_period_start": subscription.current_period_start.isoformat()
        if subscription.current_period_start
        else None,
        "current_period_end": subscription.current_period_end.isoformat()
        if subscription.current_period_end
        else None,
        "cancel_at_period_end": subscription.cancel_at_period_end,
    }


@require_GET
@login_required
def subscription_view(request):
    """
    GET /api/subscription/ -- the caller's own subscription state, or the
    FREE-fallback shape if they have none. Lazily reconciles an expired-
    by-timestamp subscription before returning (Step 12: derive effective
    state from timestamps rather than depending only on a background job).
    """
    subscription = _get_live_subscription(request.user)
    if subscription is not None:
        subscription = subscription_service.sync_subscription(subscription)
        if subscription.status not in Subscription.LIVE_STATUSES:
            subscription = None  # just expired -- fall through to FREE shape

    fallback_plan = entitlement_service.get_current_plan(request.user, None)
    return JsonResponse(_serialize(subscription, fallback_plan=fallback_plan))


@require_POST
@login_required
def subscription_cancel_view(request):
    """
    POST /api/subscription/cancel/ -- body: {"immediate": bool} (default
    false). Only ever acts on the caller's own live subscription.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)
    immediate = bool(body.get("immediate", False))

    subscription = _get_live_subscription(request.user)
    if subscription is None:
        return JsonResponse({"error": "no active subscription"}, status=404)

    try:
        if immediate:
            subscription = subscription_service.cancel_subscription(
                subscription=subscription, performed_by=request.user, source="user"
            )
        else:
            subscription = subscription_service.schedule_cancellation(
                subscription=subscription, performed_by=request.user, source="user"
            )
    except InvalidTransitionError as exc:
        return JsonResponse({"error": str(exc)}, status=409)

    return JsonResponse(_serialize(subscription, fallback_plan="FREE"))


@require_POST
@login_required
def subscription_restore_view(request):
    """POST /api/subscription/restore/ -- reverses a scheduled
    cancel-at-period-end on the caller's own subscription. Cannot resurrect
    an already-CANCELLED/EXPIRED subscription (those are terminal)."""
    subscription = _get_live_subscription(request.user)
    if subscription is None:
        return JsonResponse({"error": "no active subscription"}, status=404)

    try:
        subscription = subscription_service.restore_subscription(
            subscription=subscription, performed_by=request.user, source="user"
        )
    except InvalidTransitionError as exc:
        return JsonResponse({"error": str(exc)}, status=409)

    return JsonResponse(_serialize(subscription, fallback_plan="FREE"))
