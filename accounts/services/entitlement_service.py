# accounts/services/entitlement_service.py
"""
PHASE 2 — The single authoritative source for "is this identity allowed to
use this feature, and under what limits". Nothing else in the project is
permitted to make this decision — views, api_views, templates, and
JavaScript all call in here rather than re-implementing any part of it
(Step 4/25: no duplicate access logic).

This module is deliberately separate from:
  - accounts/services/credit_service.py   ("can this be paid for")
  - accounts/rate_limit.py                ("too many requests too fast")
Entitlement is checked BEFORE either of those for a given AI request (see
AI_GENERATORS/api_views.py) — an unentitled request never reaches rate
limiting's counters or a credit reservation (Step 11).

Public surface:
    get_current_plan(user, guest_key)              -> PlanCode (str)
    get_feature_entitlement(user, guest_key, feature) -> PlanFeatureRule | None
    get_usage_count(user, guest_key, feature, period) -> int
    can_access(user, guest_key, feature)            -> bool
    get_limit(user, guest_key, feature)              -> dict
    get_access_decision(user, guest_key, feature)    -> AccessDecision
    record_usage(user, guest_key, feature)           -> None
    get_entitlement_summary(user, guest_key)         -> dict

Identity contract matches guest_identity.identify_requester(): exactly one
of `user` (an authenticated django.contrib.auth.models.User) or `guest_key`
(a server-issued session key) is non-None. Nothing here ever trusts a
client-supplied plan/feature/user identifier — see AccessDecision and the
security tests in accounts/tests/test_entitlement_security.py.

PERFORMANCE (Step 21): the public functions above each do their own,
independent DB lookups -- convenient to call individually, but calling
several of them back-to-back (as get_access_decision used to, and as
get_entitlement_summary does for every feature) re-fetches the same
Feature/UserEntitlement rows repeatedly. Internally, both of those now
route through the same one-shot resolution helpers below
(_resolve_feature_row, which is cache-backed since the Feature registry is
effectively static catalog data, and _resolve_identity, which fetches the
user's active entitlement at most once per call) so a 5-feature summary
does a small, fixed number of queries rather than 5x the single-feature
cost. This does NOT cache per-user entitlement decisions -- only the
static Feature catalog is cached -- so expiry/revocation still take effect
immediately (see _resolve_feature_row's docstring for the exact TTL and
invalidation story).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.cache import cache
from django.utils import timezone

from accounts import entitlement_config
from accounts.models import Feature, Plan, PlanFeatureRule, UserEntitlement
from accounts.services.exceptions import InvalidIdentityError, UnknownFeatureError

GUEST_PLAN_CODE = entitlement_config.GUEST_PLAN_CODE
FREE_PLAN_CODE = Plan.Code.FREE
PREMIUM_PLAN_CODE = Plan.Code.PREMIUM


# ============================================================================
# Access decision — structured, not just True/False (Step 5)
# ============================================================================

class DenialReason:
    """Stable, machine-readable reason codes. Never expose internals beyond
    these — see Step 5/23 (don't leak sensitive internal information)."""

    FEATURE_DISABLED = "FEATURE_DISABLED"
    PLAN_NOT_ALLOWED = "PLAN_NOT_ALLOWED"
    ENTITLEMENT_INACTIVE = "ENTITLEMENT_INACTIVE"
    ENTITLEMENT_EXPIRED = "ENTITLEMENT_EXPIRED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    GUEST_RESTRICTION = "GUEST_RESTRICTION"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"


@dataclass
class AccessDecision:
    allowed: bool
    plan: str
    feature: str
    reason: str | None = None  # one of DenialReason.* — None iff allowed
    limits: dict = field(default_factory=dict)  # {"daily_limit":..,"monthly_limit":..,"per_request_limit":..}
    remaining: dict = field(default_factory=dict)  # {"daily_remaining":..,"monthly_remaining":..}

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "plan": self.plan,
            "feature": self.feature,
            "reason": self.reason,
            "limits": self.limits,
            "remaining": self.remaining,
        }


# ============================================================================
# Identity / plan resolution
# ============================================================================

def _check_identity(user, guest_key):
    if bool(user) == bool(guest_key):
        # Both None or both truthy -- guest_key="" from a not-yet-created
        # session is falsy, which correctly routes to "invalid" only when
        # user is also None, matching guest_identity's own contract.
        raise InvalidIdentityError("Exactly one of user or guest_key must be provided.")


def _get_active_entitlement(user) -> UserEntitlement | None:
    """
    Returns the user's current, time-window-valid, status=ACTIVE
    entitlement with the highest-priority plan (Premium > Free), or None.
    Suspended/inactive/expired rows never count, even though they still
    exist in the table for history (Step 18/26 boundary correctness).

    NEVER cached -- this is exactly the per-user decision Step 21 warns
    must invalidate correctly on expiry/revocation, so it's always a fresh
    query. Only the static Feature catalog (below) is cached.
    """
    now = timezone.now()
    candidates = (
        UserEntitlement.objects.filter(user=user, status=UserEntitlement.Status.ACTIVE)
        .select_related("plan")
        .order_by("-plan__display_order")
    )
    for entitlement in candidates:
        if entitlement.effective_status(at=now) == "ACTIVE":
            return entitlement
    return None


def _resolve_identity(user, guest_key) -> tuple[str, UserEntitlement | None]:
    """
    One-shot identity resolution: returns (plan_code, active_entitlement).
    active_entitlement is always None for guests and for authenticated
    users with no valid grant (i.e. plan_code == FREE in that case).
    Every caller that needs "the plan" AND might need "the entitlement
    object" (to check its window/status) should call this once and reuse
    both, rather than resolving plan and re-fetching entitlement
    separately -- that duplication was Phase 2's original N+1 bug (see
    this module's docstring).
    """
    _check_identity(user, guest_key)
    if user is None:
        return GUEST_PLAN_CODE, None
    entitlement = _get_active_entitlement(user)
    if entitlement is not None:
        return entitlement.plan.code, entitlement
    return FREE_PLAN_CODE, None


def get_current_plan(user=None, guest_key=None) -> str:
    """
    Resolves an identity's current plan code. Server-authoritative only —
    never reads a plan value from request data (Step 7/8/22).

    Guest        -> GUEST (virtual, no DB row -- Step 6)
    Authenticated with a real ACTIVE (time-window-valid) entitlement -> that
        entitlement's plan code
    Authenticated with no such entitlement -> FREE (Step 7 default)
    """
    plan_code, _entitlement = _resolve_identity(user, guest_key)
    return plan_code


def get_entitlement_status(user) -> str:
    """
    Returns the effective_status ("ACTIVE"/"INACTIVE"/"EXPIRED"/"SUSPENDED")
    of the user's most relevant non-FREE entitlement, or "ACTIVE" (implicit,
    for the FREE default) if none exists. Used by the read-only
    entitlements API (Step 12) to surface *why* a user is on FREE, e.g. an
    expired Premium grant, without exposing raw model internals.
    """
    if user is None:
        return "ACTIVE"
    now = timezone.now()
    latest = (
        UserEntitlement.objects.filter(user=user)
        .exclude(plan__code=FREE_PLAN_CODE)
        .order_by("-created_at")
        .select_related("plan")
        .first()
    )
    if latest is None:
        return "ACTIVE"
    return latest.effective_status(at=now)


# ============================================================================
# Feature / rule lookup
# ============================================================================

_FEATURE_CACHE_TTL = 300  # seconds -- see _resolve_feature_row docstring


def _feature_cache_key(code: str) -> str:
    return f"entitlement:feature_row:{code}"


def _resolve_feature_row(feature: str) -> Feature:
    """
    Accepts either a Feature.code (e.g. "AI_CHAT") or the AIUsage slug
    (e.g. "chat") the AI endpoint already speaks, and returns the Feature
    row. Raises UnknownFeatureError for anything else -- Step 22: never
    silently fall through on an unrecognized feature.

    Cached for _FEATURE_CACHE_TTL (Step 21: "cached immutable plan/feature
    definitions where appropriate") -- the Feature registry is edited only
    through the Django admin, essentially never, and caching it is what
    takes get_entitlement_summary()'s 5-feature loop from 5 redundant
    identical queries down to (worst case) 5 cache reads. This cache is
    query-shaped, not decision-shaped: it never stores whether access is
    allowed, only the Feature row's own fields (code/name/is_active), so
    it cannot go stale in a way that grants access that shouldn't exist --
    contrast with _get_active_entitlement above, which is intentionally
    NEVER cached because THAT is the per-user decision that must
    invalidate immediately on expiry/suspension/revocation.
    """
    code = entitlement_config.SLUG_TO_FEATURE_CODE.get(feature, feature)
    cache_key = _feature_cache_key(code)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        feature_row = Feature.objects.get(code=code)
    except Feature.DoesNotExist:
        raise UnknownFeatureError(f"Unknown feature: {feature!r}")
    cache.set(cache_key, feature_row, _FEATURE_CACHE_TTL)
    return feature_row


def _resolve_feature(feature: str) -> Feature:
    # Backwards-compatible internal alias -- kept short since this is
    # called from several places in this module.
    return _resolve_feature_row(feature)


def get_feature_entitlement(user, guest_key, feature: str) -> PlanFeatureRule | None:
    """Returns the PlanFeatureRule that applies to this identity's current
    plan for this feature, or None if no rule exists (== not entitled)."""
    plan_code, _entitlement = _resolve_identity(user, guest_key)
    feature_row = _resolve_feature_row(feature)

    if plan_code == GUEST_PLAN_CODE:
        # Guests don't have PlanFeatureRule rows (no Plan row for GUEST at
        # all -- Step 6). Guest access is a fixed, code-level policy: every
        # registered, active Feature is reachable by guests, gated only by
        # GUEST_DAILY_LIMIT below and by the existing rate limiter.
        return None

    return _get_rule(plan_code, feature_row)


def _get_rule(plan_code: str, feature_row: Feature) -> PlanFeatureRule | None:
    try:
        return PlanFeatureRule.objects.select_related("plan", "feature").get(
            plan__code=plan_code, feature=feature_row
        )
    except PlanFeatureRule.DoesNotExist:
        return None


# ============================================================================
# Usage counters (Step 16/17) — efficient, timezone-aware, idempotent
# ============================================================================
#
# Backed by Django's cache framework, matching accounts/rate_limit.py's
# existing convention/limitation (LocMemCache is per-process by default;
# see that module's docstring -- the same production note applies here
# unchanged, not re-litigated). This is a request-count QUOTA, not a
# reservation and not a security-critical lock: an undercount only ever
# costs a plan a slightly-too-generous ceiling for one cache TTL, it never
# grants credit or bypasses the credit engine, so cache-based counting
# (rather than one DB row per request) is an appropriate, documented
# tradeoff -- see PHASE_2_ENTITLEMENT_ENGINE.md "Quota architecture".

def _period_key(user, guest_key, feature_code: str, period: str) -> str:
    identity = f"user:{user.pk}" if user is not None else f"guest:{guest_key}"
    now = timezone.now()
    if period == "daily":
        bucket = now.strftime("%Y-%m-%d")
    elif period == "monthly":
        bucket = now.strftime("%Y-%m")
    else:
        raise ValueError(f"Unknown period: {period!r}")
    return f"entitlement:usage:{period}:{identity}:{feature_code}:{bucket}"


def _period_ttl(period: str) -> int:
    if period == "daily":
        return 60 * 60 * 26  # a bit over a day, so a late read near midnight is still valid
    return 60 * 60 * 24 * 32  # a bit over a month


def get_usage_count(user, guest_key, feature: str, period: str) -> int:
    _check_identity(user, guest_key)
    feature_row = _resolve_feature_row(feature)
    key = _period_key(user, guest_key, feature_row.code, period)
    return cache.get(key, 0)


def record_usage(user, guest_key, feature: str) -> None:
    """
    Increments both the daily and monthly counters for this identity/
    feature. Called once per request that actually reaches the AI provider
    (see AI_GENERATORS/api_views.py) — never on a request entitlement or
    quota already rejected, so a denied request doesn't consume budget it
    was never granted (mirrors the credit engine's "never charge for a
    request the server refused" principle, applied to quota instead of
    credits).
    """
    _check_identity(user, guest_key)
    feature_row = _resolve_feature_row(feature)
    for period in ("daily", "monthly"):
        key = _period_key(user, guest_key, feature_row.code, period)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, _period_ttl(period))


# ============================================================================
# Limits
# ============================================================================

def get_limit(user, guest_key, feature: str) -> dict:
    """
    Returns {"daily_limit": int|None, "monthly_limit": int|None,
    "per_request_limit": int|None} for this identity/feature. None means
    unlimited for that dimension.
    """
    plan_code, _entitlement = _resolve_identity(user, guest_key)

    if plan_code == GUEST_PLAN_CODE:
        return {
            "daily_limit": entitlement_config.GUEST_DAILY_LIMIT or None,
            "monthly_limit": None,
            "per_request_limit": None,
        }

    feature_row = _resolve_feature_row(feature)
    rule = _get_rule(plan_code, feature_row)
    if rule is None:
        return {"daily_limit": 0, "monthly_limit": 0, "per_request_limit": 0}
    return {
        "daily_limit": rule.daily_limit,
        "monthly_limit": rule.monthly_limit,
        "per_request_limit": rule.per_request_limit,
    }


# ============================================================================
# can_access / get_access_decision — the core decision
# ============================================================================

def can_access(user, guest_key, feature: str) -> bool:
    return get_access_decision(user, guest_key, feature).allowed


def get_access_decision(user, guest_key, feature: str) -> AccessDecision:
    """
    The single canonical access decision (Step 4/5). Order of checks:
      1. feature must exist and be active
      2. resolve plan (server-authoritative identity only)
      3. guest -> fixed guest policy + guest daily quota
      4. authenticated, non-FREE plan -> entitlement window/status must be
         ACTIVE (not before-start, not expired, not suspended)
      5. plan-feature rule must exist and be ALLOWED
      6. quota (daily/monthly) must not be exceeded
    First failing check wins and returns immediately with its reason.
    """
    plan_code, entitlement = _resolve_identity(user, guest_key)
    try:
        feature_row = _resolve_feature_row(feature)
    except UnknownFeatureError:
        return AccessDecision(
            allowed=False, plan=plan_code, feature=str(feature), reason=DenialReason.FEATURE_DISABLED
        )
    return _decide(user, guest_key, plan_code, entitlement, feature_row)


def _decide(user, guest_key, plan_code: str, entitlement: UserEntitlement | None, feature_row: Feature) -> AccessDecision:
    """Shared implementation used by both get_access_decision() (resolves
    identity itself) and get_entitlement_summary() (resolves identity once,
    reuses it across all features) -- see this module's docstring."""

    if not feature_row.is_active:
        return AccessDecision(
            allowed=False, plan=plan_code, feature=feature_row.code, reason=DenialReason.FEATURE_DISABLED
        )

    # ── Guest path ───────────────────────────────────────────────────────
    # PHASE 8A CANONICAL RULE (supersedes the prior guest-quota design
    # below): an anonymous visitor must never be allowed to consume real
    # AI-generation capacity. Every Feature row in this table is
    # Category.AI_GENERATOR (see Feature model docstring) -- there is no
    # non-generation feature this table is asked to gate -- so the guest
    # branch denies unconditionally, before any quota is even computed.
    #
    # OLD RULE (Phase 2-8, now retired): guests were allowed up to
    # GUEST_DAILY_LIMIT (entitlement_config.py) requests per feature per
    # day, denied only past that count with DenialReason.GUEST_RESTRICTION.
    # This was reachable code with its own passing tests
    # (test_entitlement_engine.py, test_ai_entitlement_integration.py,
    # test_phase8_guest_credit_migration.py) -- not a latent/dead bug --
    # and it is what actually produced the reported "0 -> 45" symptom:
    # allowed=True here let AI_GENERATORS/api_views.py:ai_generate() reach
    # ai_usage_service.begin_request(), whose
    # credit_service.get_or_create_account(guest_key=...) lazily creates a
    # guest CreditAccount and grants it INITIAL_FREE_CREDITS (50) on first
    # touch, then reserves the request's real cost (5) against it -- a
    # real ledger entry on a real (guest-scoped) account, not a display
    # bug, but one the product owner has now ruled unacceptable: an
    # unauthenticated visitor must not silently receive or spend
    # AI-generation credits at all.
    #
    # WHY THE NEW ASSERTION IS CORRECT: DenialReason.AUTHENTICATION_REQUIRED
    # already existed (defined above) with full frontend wiring already in
    # place and unit-tested (templates/main.html's copy map,
    # AI_GENERATORS/api_views.py's _DENIAL_STATUS/_DENIAL_MESSAGE at 401,
    # test_ai_credit_exhaustion_ui.py, test_entitlement_ux.py) but was
    # never actually returned by this function -- a half-wired feature,
    # not new plumbing. Returning it here for every guest request means
    # _check_access_policy() in AI_GENERATORS/api_views.py short-circuits
    # with a 401 before rate limiting, before request_id/prompt parsing,
    # and — critically — before ai_usage_service.begin_request() is ever
    # called, so no CreditAccount is created for a guest, no credits are
    # granted or reserved, and the provider is never invoked. This matches
    # the canonical gate order (AUTH -> ENTITLEMENT -> CREDIT ELIGIBILITY
    # -> RESERVATION -> PROVIDER) for every one of photo/story/
    # application/proposal/code, since none of them special-case guest
    # access differently (there is no per-Feature "guest allowed" flag on
    # the Feature model).
    #
    # Guest SESSION identity (accounts/services/guest_identity.py) is
    # untouched by this change and continues to exist -- it is not the
    # same thing as AI-generation authorization, and other code (CSRF,
    # the (now guest-unreachable but still correct)
    # migrate_guest_credits_to_user() path for any pre-existing guest
    # balance created before this fix shipped) still depends on it.
    if plan_code == GUEST_PLAN_CODE:
        return AccessDecision(
            allowed=False,
            plan=plan_code,
            feature=feature_row.code,
            reason=DenialReason.AUTHENTICATION_REQUIRED,
            limits={"daily_limit": None, "monthly_limit": None, "per_request_limit": None},
            remaining={},
        )

    # ── Authenticated, non-FREE plan: entitlement window must be valid ───
    if plan_code != FREE_PLAN_CODE:
        status = entitlement.effective_status() if entitlement else "INACTIVE"
        if status == "EXPIRED":
            return AccessDecision(
                allowed=False, plan=plan_code, feature=feature_row.code, reason=DenialReason.ENTITLEMENT_EXPIRED
            )
        if status != "ACTIVE":
            return AccessDecision(
                allowed=False, plan=plan_code, feature=feature_row.code, reason=DenialReason.ENTITLEMENT_INACTIVE
            )

    # ── Plan-feature rule ────────────────────────────────────────────────
    rule = _get_rule(plan_code, feature_row)
    if rule is None or rule.access != PlanFeatureRule.Access.ALLOWED:
        return AccessDecision(
            allowed=False, plan=plan_code, feature=feature_row.code, reason=DenialReason.PLAN_NOT_ALLOWED
        )

    limits = {
        "daily_limit": rule.daily_limit,
        "monthly_limit": rule.monthly_limit,
        "per_request_limit": rule.per_request_limit,
    }

    # ── Quota ────────────────────────────────────────────────────────────
    remaining = {}
    if rule.daily_limit is not None:
        used = get_usage_count(user, guest_key, feature_row.code, "daily")
        if used >= rule.daily_limit:
            return AccessDecision(
                allowed=False,
                plan=plan_code,
                feature=feature_row.code,
                reason=DenialReason.QUOTA_EXCEEDED,
                limits=limits,
                remaining={"daily_remaining": 0},
            )
        remaining["daily_remaining"] = rule.daily_limit - used

    if rule.monthly_limit is not None:
        used = get_usage_count(user, guest_key, feature_row.code, "monthly")
        if used >= rule.monthly_limit:
            return AccessDecision(
                allowed=False,
                plan=plan_code,
                feature=feature_row.code,
                reason=DenialReason.QUOTA_EXCEEDED,
                limits=limits,
                remaining={**remaining, "monthly_remaining": 0},
            )
        remaining["monthly_remaining"] = rule.monthly_limit - used

    return AccessDecision(allowed=True, plan=plan_code, feature=feature_row.code, limits=limits, remaining=remaining)


# ============================================================================
# Read-only summary for the entitlements/profile APIs (Step 12/15)
# ============================================================================

def get_entitlement_summary(user, guest_key) -> dict:
    """
    Everything the authenticated caller's own /api/entitlements/ or
    /profile/data/ response needs — plan, per-feature access + limits +
    remaining quota, and (for authenticated users) the underlying
    entitlement's expiry, if any. Identity is always the resolved
    session/user, never a client-supplied id (Step 12/22).

    Resolves identity (plan + entitlement) ONCE and reuses it across every
    registered feature, rather than each feature re-triggering its own
    identity resolution -- see this module's docstring re: N+1.
    """
    plan_code, entitlement = _resolve_identity(user, guest_key)

    features = []
    for feature_code, _slug, name in entitlement_config.FEATURE_DEFINITIONS:
        feature_row = _resolve_feature_row(feature_code)
        decision = _decide(user, guest_key, plan_code, entitlement, feature_row)
        features.append(
            {
                "feature": feature_code,
                "name": name,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "limits": decision.limits,
                "remaining": decision.remaining,
            }
        )

    expires_at = entitlement.expires_at if entitlement else None
    entitlement_status = entitlement.effective_status() if entitlement else "ACTIVE"

    return {
        "plan": plan_code,
        "entitlement_status": entitlement_status,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "features": features,
    }
