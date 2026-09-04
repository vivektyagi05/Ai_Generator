# accounts/entitlement_views.py
"""
PHASE 2 Step 12/13 — read-only entitlement & plan-catalog APIs.

Both endpoints are GET-only, safe (no side effects), and never accept a
user_id / plan_id / feature identifier from the client to decide whose
data comes back -- identity is always the authenticated session or
server-issued guest session (accounts.services.guest_identity), exactly
like every other endpoint in this project (Step 12/22).
"""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from accounts.models import Plan, PlanFeatureRule
from accounts.services import entitlement_service, guest_identity
from accounts import credit_config, entitlement_config


@require_GET
def entitlements_view(request):
    """
    GET /api/entitlements/ -- the current caller's own plan, per-feature
    access, limits, and remaining quota. Never exposes internal primary
    keys, other users' data, or payment internals (Step 12).
    """
    user, guest_key = guest_identity.identify_requester(request)
    summary = entitlement_service.get_entitlement_summary(user, guest_key)
    return JsonResponse(summary)


@require_GET
def plans_view(request):
    """
    GET /api/plans/ -- public, read-only plan catalog. Only active,
    public plans; only feature codes/names + limits, never internal ids,
    admin notes, or payment/provider data (Step 13).
    """
    plans = Plan.objects.filter(is_active=True, is_public=True).order_by("display_order", "code")
    rules = (
        PlanFeatureRule.objects.filter(plan__in=plans)
        .select_related("feature")
        .order_by("feature__code")
    )
    rules_by_plan: dict[int, list] = {}
    for rule in rules:
        rules_by_plan.setdefault(rule.plan_id, []).append(
            {
                "feature": rule.feature.code,
                "name": rule.feature.name,
                "access": rule.access,
                "limits": {
                    "daily_limit": rule.daily_limit,
                    "monthly_limit": rule.monthly_limit,
                    "per_request_limit": rule.per_request_limit,
                },
            }
        )

    payload = {
        "plans": [
            {
                "code": plan.code,
                "name": plan.name,
                "description": plan.description,
                "features": rules_by_plan.get(plan.id, []),
            }
            for plan in plans
        ]
    }
    return JsonResponse(payload)


@require_GET
def costs_view(request):
    """
    GET /api/ai/costs/ -- PHASE 7 STEP 7.4 (cost transparency).

    Public, read-only, per-feature credit-cost info for the generator
    workspace to show BEFORE submission -- addresses the real gap the
    Phase 7 forensic pass found: nothing in Phase 6 exposed cost to the
    frontend at all, so `main.html` had no honest number to show and (per
    RULE 11 / the "backend remains authoritative" instruction) is not
    allowed to invent or hardcode one.

    Computes every figure through the SAME function
    accounts.services.ai_usage_service already calls at actual reservation
    time -- accounts.credit_config.estimate_reservation_credits() -- so
    this is not a second, frontend-facing cost formula that could drift
    from what a request is really charged; it is that formula, exposed.

    For flat-cost features (currently just "photo", see
    credit_config.FLAT_COST_FEATURES) the number returned is the exact
    flat charge. For token-based features the real cost depends on the
    actual prompt/response length and can only be known once a request is
    reserved, so what's returned is the real reservation ESTIMATE for a
    minimal (empty) prompt -- reported honestly as "minimum_credits" with
    billing: "token_based", never as if it were the final price for an
    actual prompt.
    """
    costs = {}
    for _code, slug, display_name in entitlement_config.FEATURE_DEFINITIONS:
        if slug in credit_config.FLAT_COST_FEATURES:
            costs[slug] = {
                "name": display_name,
                "billing": "flat",
                "credits": credit_config.estimate_reservation_credits("", feature=slug),
            }
        else:
            costs[slug] = {
                "name": display_name,
                "billing": "token_based",
                "minimum_credits": credit_config.estimate_reservation_credits("", feature=slug),
            }
    return JsonResponse({"costs": costs})
