# accounts/entitlement_config.py
"""
PHASE 2 — Central, server-side plan/feature matrix.

Business values live here, never buried in views/services/templates
(mirrors accounts/credit_config.py's and accounts/ai_request_config.py's
existing pattern). Every number is a documented PLACEHOLDER — Phase 0/
product has not established real business figures for this project any
more than it had for the credit engine — but the *shape* (which features
exist, which plans exist, that limits are request-count quotas independent
of credits) is real, taken directly from the Phase 1 codebase, not
invented.

Overridable from Django settings / the environment, same convention as the
rest of the project:

    ENTITLEMENT_FREE_DAILY_LIMIT = 15
    ENTITLEMENT_FREE_MONTHLY_LIMIT = 200
    ENTITLEMENT_PREMIUM_DAILY_LIMIT = 0        # 0 == unlimited
    ENTITLEMENT_PREMIUM_MONTHLY_LIMIT = 0
    ENTITLEMENT_GUEST_DAILY_LIMIT = 5
"""

from __future__ import annotations

from django.conf import settings

# ============================================================================
# Feature registry — maps 1:1 onto accounts/ai_request_config.ALLOWED_FEATURES.
# Nothing here describes a capability that doesn't exist in the codebase.
# ============================================================================

# (feature_code, ai_usage_feature_slug, display_name)
#
# PHASE 1 — GENERATOR ARCHITECTURE NORMALIZATION: AI_PHOTO added here so the
# Photo Generator is registered in the exact same catalog as the other four
# generators, gets a PlanFeatureRule seeded for every plan (see the seeding
# migration), and participates in get_access_decision() like everything
# else. Before this phase, "photo" did not exist in this file, which is
# exactly why it was possible for the frontend to call a third-party image
# API directly without ever being checked here.
FEATURE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("AI_CHAT", "chat", "AI Chat"),
    ("AI_CODE", "code", "AI Code Generator"),
    ("AI_STORY", "story", "AI Story Generator"),
    ("AI_APPLICATION", "application", "AI Application Writer"),
    ("AI_PROPOSAL", "proposal", "AI Proposal Writer"),
    ("AI_PHOTO", "photo", "AI Photo Generator"),
)

# feature_code -> AIUsage.feature slug, and the reverse — used by
# entitlement_service to translate the slug the AI endpoint already speaks
# (accounts/ai_request_config.py) into the Feature.code the entitlement
# engine speaks, without either module needing to know the other's naming.
FEATURE_CODE_TO_SLUG: dict[str, str] = {code: slug for code, slug, _ in FEATURE_DEFINITIONS}
SLUG_TO_FEATURE_CODE: dict[str, str] = {slug: code for code, slug, _ in FEATURE_DEFINITIONS}


# ============================================================================
# Plan catalog
# ============================================================================

PLAN_DEFINITIONS: tuple[dict, ...] = (
    {
        "code": "FREE",
        "name": "Free",
        "description": "Default plan for every authenticated account.",
        "display_order": 1,
        "is_public": True,
    },
    {
        "code": "PREMIUM",
        "name": "Premium",
        "description": "Higher usage limits across all AI generators. "
        "Activation is not available yet — see the public plan page.",
        "display_order": 2,
        "is_public": True,
    },
)

# GUEST is intentionally NOT a Plan row (Step 6) — it's resolved purely in
# code by entitlement_service for any identity with no authenticated user.
GUEST_PLAN_CODE = "GUEST"


# ============================================================================
# Limits (Step 16/17) — request-count quotas, independent of CreditAccount.
# 0 or None means "no limit enforced for this dimension".
# ============================================================================

# These sit deliberately ABOVE accounts/rate_limit.py's existing AI caps
# (AI_GUEST_SESSION_MAX_PER_WINDOW=8/hr, AI_AUTH_MAX_PER_WINDOW=40/hr) so
# that, with these placeholder defaults, the rate limiter -- not the
# entitlement quota -- is normally the first thing an abusive burst hits.
# Quota and rate limit are still independent, separately-enforced decisions
# (Step 10); this is just a documented choice of *default numbers* so the
# two don't fight over which one "wins" first at the out-of-the-box values.
# A real product decision can freely set ENTITLEMENT_GUEST_DAILY_LIMIT /
# ENTITLEMENT_FREE_DAILY_LIMIT below the rate-limit numbers if the intent
# actually is for quota to be the tighter, earlier gate.
GUEST_DAILY_LIMIT: int = int(getattr(settings, "ENTITLEMENT_GUEST_DAILY_LIMIT", 30))

FREE_DAILY_LIMIT: int = int(getattr(settings, "ENTITLEMENT_FREE_DAILY_LIMIT", 200))
FREE_MONTHLY_LIMIT: int = int(getattr(settings, "ENTITLEMENT_FREE_MONTHLY_LIMIT", 3000))

# 0 == unlimited for Premium, by design (Step 9: "higher limits where
# explicitly defined" — Premium's defining benefit in this placeholder
# matrix is the removal of the request-count ceiling; credits and rate
# limiting still apply regardless of plan).
PREMIUM_DAILY_LIMIT: int = int(getattr(settings, "ENTITLEMENT_PREMIUM_DAILY_LIMIT", 0))
PREMIUM_MONTHLY_LIMIT: int = int(getattr(settings, "ENTITLEMENT_PREMIUM_MONTHLY_LIMIT", 0))


def plan_feature_rules_for(plan_code: str) -> list[dict]:
    """
    Returns the list of per-feature rule dicts (matching PlanFeatureRule
    fields) that should exist for a given plan code. Used only by the
    seeding data migration — the authoritative runtime source is always the
    PlanFeatureRule table itself, never this function re-evaluated live.
    """
    if plan_code == "FREE":
        daily, monthly = FREE_DAILY_LIMIT, FREE_MONTHLY_LIMIT
    elif plan_code == "PREMIUM":
        daily, monthly = (PREMIUM_DAILY_LIMIT or None), (PREMIUM_MONTHLY_LIMIT or None)
    else:
        raise ValueError(f"No seed rule set defined for plan_code={plan_code!r}")

    return [
        {
            "feature_code": code,
            "access": "ALLOWED",
            "daily_limit": daily,
            "monthly_limit": monthly,
            "per_request_limit": None,
        }
        for code, _slug, _name in FEATURE_DEFINITIONS
    ]
