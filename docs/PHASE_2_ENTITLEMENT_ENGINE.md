# PHASE 2 — ENTITLEMENT & PLAN ENGINE

## Architecture

Three layers, each independently swappable and each already used elsewhere
in this project's own vocabulary (Step 10):

| Question | Answered by | Module |
|---|---|---|
| Can this be **paid for**? | CreditAccount balance | `accounts/services/credit_service.py` (Phase 1, unchanged) |
| Is this identity **allowed** to use this feature at all? | Plan → Feature → PlanFeatureRule → UserEntitlement | `accounts/services/entitlement_service.py` (Phase 2, new) |
| Has this identity made **too many requests too fast**? | Cache-backed sliding counters | `accounts/rate_limit.py` (Phase 1, unchanged) |

These three never merge into one decision and never call into each other.
The AI endpoint (`AI_GENERATORS/api_views.py`) asks all three, in that
entitlement → rate-limit → credit order (see "Request lifecycle" below),
and only proceeds if every one says yes.

## Domain model

```
Plan (catalog: FREE, PREMIUM — GUEST is virtual, no row)
  |
  +-- PlanFeatureRule (plan, feature) -> access, daily/monthly/per-request limit
  |     |
  |     +-- Feature (catalog: AI_CHAT, AI_CODE, AI_STORY, AI_APPLICATION, AI_PROPOSAL)
  |
  +-- UserEntitlement (user, plan, status, starts_at, expires_at, source)
        |
        +-- EntitlementAuditLog (append-only: every admin grant/revoke/suspend/reactivate/extend)
```

- **Plan** — a catalog row describing what a plan *offers*. A Premium row
  existing never means anyone has Premium (Step 8's core invariant,
  verified by `PremiumPlanTests.test_premium_row_existing_does_not_automatically_grant_access`).
- **Feature** — one row per real, existing AI generator. Maps 1:1 onto
  `accounts/ai_request_config.ALLOWED_FEATURES` slugs
  (`accounts/entitlement_config.py::FEATURE_CODE_TO_SLUG` /
  `SLUG_TO_FEATURE_CODE`) so the entitlement engine and the AI endpoint
  speak the same features under different names without either module
  needing to know the other's naming convention.
- **PlanFeatureRule** — what a plan actually provides for a feature:
  ALLOWED/DISABLED plus three independent limit dimensions. `(plan,
  feature)` is unique (`uq_planfeaturerule_plan_feature`). Absence of a
  row means **not entitled** — the engine fails closed, never open.
- **UserEntitlement** — the concrete, time-boxed grant of a plan to a real
  user. Guests never get a row (Step 6). FREE is not normally stored
  either — an authenticated user with no row resolves to FREE by default
  (Step 7); a row only exists for a plan that needed an explicit grant,
  which today means Premium only, created only through
  `accounts/services/entitlement_admin.py` (admin-audited) or test
  fixtures — never a client endpoint (Step 8).
- **EntitlementAuditLog** — append-only (admin `has_change_permission`/
  `has_delete_permission` both return `False`, mirroring
  `CreditTransaction`'s pattern), one row per grant/revoke/suspend/
  reactivate/extend, always with a required `reason` and the acting staff
  user.

### Why EXPIRED isn't a stored status

`UserEntitlement.Status` only has ACTIVE/INACTIVE/SUSPENDED.
"EXPIRED" is a **computed** property
(`UserEntitlement.effective_status()`), derived from `expires_at` vs
`timezone.now()` at read time. A stored, separately-updated EXPIRED status
could drift out of sync with the clock (a batch job that hasn't run yet,
a Premium grant read one second after `expires_at`); computing it on every
read makes that drift structurally impossible. Boundary rule: `at >=
expires_at` is expired (tested at the exact instant and one second before
in `test_expiration_exactly_at_boundary_is_expired` /
`test_expiration_one_second_before_boundary_is_active`).

## Access decision (Step 5)

`entitlement_service.get_access_decision(user, guest_key, feature)` never
returns a bare bool. It returns an `AccessDecision`:

```python
AccessDecision(
    allowed: bool,
    plan: str,
    feature: str,
    reason: str | None,   # one of DenialReason.* -- None iff allowed
    limits: dict,          # {"daily_limit":.., "monthly_limit":.., "per_request_limit":..}
    remaining: dict,        # {"daily_remaining":.., "monthly_remaining":..}
)
```

Denial reasons (stable, machine-readable — never internal exception text):
`FEATURE_DISABLED`, `PLAN_NOT_ALLOWED`, `ENTITLEMENT_INACTIVE`,
`ENTITLEMENT_EXPIRED`, `QUOTA_EXCEEDED`, `GUEST_RESTRICTION`,
`AUTHENTICATION_REQUIRED`.

Checks run in this order, first failure wins:
1. feature exists and is active
2. resolve plan (guest → GUEST; authenticated → active entitlement's plan, else FREE)
3. guest → fixed guest policy + `GUEST_DAILY_LIMIT`
4. authenticated non-FREE plan → entitlement window/status must be ACTIVE
5. plan-feature rule must exist and be ALLOWED
6. daily/monthly quota must not be exceeded

## Guest / Free / Premium behavior

- **Guest** (Step 6) — resolved purely in code from
  `guest_identity.identify_requester()`'s server-issued session key. No DB
  row, no client-controlled value can produce it or spoof it (see
  `GuestResolutionTests.test_guest_cannot_spoof_free_or_premium` and
  `test_entitlement_security.py::GuestSpoofingTests`). Gated by
  `GUEST_DAILY_LIMIT` (default 30/day) plus the existing session/IP rate
  limiter — two independent, layered controls.
- **Free** (Step 7) — the default for any authenticated user with no
  active entitlement row. Never inferred from email/username/cookies/query
  params — always `_resolve_identity()`'s DB lookup.
- **Premium** (Step 8) — a real catalog Plan and a real PlanFeatureRule
  set, but **no activation path exists**. The only way a
  `UserEntitlement(plan=PREMIUM)` row is created is:
  `accounts/services/entitlement_admin.py::grant_entitlement()`, called
  only from the Django admin (staff-only, authenticated, itself logged by
  Django) — or a test fixture inside a rolled-back transaction. There is
  no URL, no API, no "make me premium" anywhere in the codebase (grep
  `grep -rn "grant_entitlement" --include=*.py .` to confirm the only
  caller is `accounts/admin.py`).

## Quota vs. credit vs. rate limit (Step 10)

Deliberately three separate counters that never see each other:

- **Credit** — `CreditAccount.balance`/`reserved_balance`, DB rows, exact,
  transactional (`select_for_update()`), Phase 1, unchanged.
- **Entitlement quota** — `entitlement_service`'s cache-backed daily/
  monthly counters (`accounts/services/entitlement_service.py`'s "Usage
  counters" section), a **request count**, independent of how many
  credits that request cost.
- **Rate limit** — `accounts/rate_limit.py`'s existing sliding-window
  cache counters, unchanged, guards against bursts within a short window
  regardless of plan or quota.

A user can have 3000 monthly AI_CHAT requests of quota remaining and 40
requests/hour of rate-limit headroom and 2 credits left — all three are
checked, any one can block a request, and none of them substitutes for
either of the others.

## Expiration (Step 18)

`starts_at` (default: now) / `expires_at` (nullable = never expires).
`UserEntitlement.effective_status(at=None)`:

```
at < starts_at                          -> INACTIVE   (not yet active)
status == SUSPENDED                     -> SUSPENDED
status != ACTIVE                        -> INACTIVE
expires_at is not None and at >= expires_at -> EXPIRED
otherwise                               -> ACTIVE
```

An expired or not-yet-active Premium entitlement does **not** deny the
request outright — `get_current_plan()` simply doesn't count it, so the
user falls back to FREE (verified live: a user with an expired Premium
grant gets `{"plan": "FREE", ...}` from `/api/entitlements/` and a normal
200 from `/api/ai/`, not an error). This matches how a real subscription
lapsing should behave: the user loses Premium's extra limits, not all
service.

## Security (Step 22)

Verified by `accounts/tests/test_entitlement_security.py` (8 tests, all
passing) plus a live manual pass (see "Manual API verification" below):

- Plan/entitlement/feature is **never** read from request body, query
  params, cookies, or headers — `get_current_plan()`'s only inputs are the
  Django `User` object from the authenticated session and a server-issued
  guest session key. A request body containing `{"plan": "PREMIUM"}`,
  `{"plan_id": "premium"}`, `{"is_premium": true}`, `{"user_id": <other>}`
  is inert — nothing in the entitlement service or the AI view reads
  those keys at all.
- `/api/entitlements/` and `/api/plans/` never accept a client-supplied
  user/plan identifier — identity always comes from
  `guest_identity.identify_requester(request)`, same as every other
  endpoint in the project.
- Cross-user: user B's `/api/entitlements/` call cannot see user A's
  Premium grant (`CrossUserEntitlementTests`).
- `/api/plans/` (public catalog) never serializes a raw Django primary
  key — only `code`/`name`/`description`/feature limits.

## API response contract (Step 23)

| Denial reason | HTTP status |
|---|---|
| `AUTHENTICATION_REQUIRED` | 401 |
| `FEATURE_DISABLED`, `PLAN_NOT_ALLOWED`, `ENTITLEMENT_INACTIVE`, `ENTITLEMENT_EXPIRED`, `GUEST_RESTRICTION` | 403 |
| `QUOTA_EXCEEDED` | 429 |

402 is never used for entitlement denial — it stays reserved for the
credit engine's own insufficient-balance response (Phase 1, unchanged),
per the spec's explicit instruction not to conflate the two.

```json
{"success": false, "error": {"code": "QUOTA_EXCEEDED", "message": "You've reached your usage limit for this feature."}}
```

## Performance (Step 21)

Initial implementation had a real N+1: `get_access_decision()` re-resolved
the identity (plan + active entitlement) up to 3 times internally, and
`get_entitlement_summary()` (5 features) multiplied that by 5 — measured
at **7 queries for a single decision, 36 for the 5-feature summary**.

Fixed by:
- `_resolve_identity(user, guest_key)` — resolves plan + active entitlement
  **once**, returned as a tuple and threaded through every internal call
  in that request instead of being re-derived.
- `_resolve_feature_row()` — the `Feature` catalog is cached (Django cache
  framework, 300s TTL) since it's edited only through the admin,
  essentially never. This is query-shaped caching of static rows, not
  decision-shaped caching of "is this user allowed" — it cannot go stale
  in a way that grants access that shouldn't exist.
- `UserEntitlement` lookups are **never** cached — this is exactly the
  per-user state that must invalidate immediately on
  expiry/suspension/revocation (Step 21: "never let stale cache grant
  expired Premium access").

Result (measured with `django.db.connection.queries` under `DEBUG=True`,
same user, same process — see PHASE_2_COMPLETION_AUDIT.md for the exact
before/after numbers): 7 → 3 for a single decision, 36 → 10 for the
5-feature summary, with the remaining queries dominated by
`PlanFeatureRule` lookups (deliberately uncached, since that table is
admin-editable and must reflect changes immediately) rather than repeated
`Feature`/`UserEntitlement` fetches.

## Caching (documented per Step 21's requirement)

| What | Cache key | TTL | Invalidation |
|---|---|---|---|
| `Feature` row by code | `entitlement:feature_row:<code>` | 300s | Time-based only. Edited via Django admin so rarely that a stale read for up to 5 minutes is an accepted, documented tradeoff — never a security decision. |
| Entitlement quota counters | `entitlement:usage:<period>:<identity>:<feature>:<bucket>` | ~26h (daily) / ~32d (monthly) | Bucket key itself changes at the period boundary (date-string suffix), so no explicit invalidation needed; a slightly-early or slightly-late read only ever affects a request-count ceiling, never credits or payment. |
| `UserEntitlement` (active grant) | **not cached** | n/a | Always a fresh query — this is the one thing that must reflect a revoke/suspend/expiry instantly. |
| `PlanFeatureRule` | **not cached** | n/a | Admin-editable; a disabled feature must take effect on the very next request. |

Same underlying cache backend as `accounts/rate_limit.py` (Django's cache
framework, `LocMemCache` by default — per-process, same documented
limitation, not re-litigated here).

## APIs

- `GET /api/entitlements/` — the caller's own plan, per-feature
  allowed/reason/limits/remaining, and entitlement status/expiry. Never
  exposes a primary key.
- `GET /api/plans/` — public catalog of active, public plans and their
  feature rules. No internal ids, no admin metadata.
- `GET /plans/` — server-rendered HTML page backed by the same catalog
  (no separate hardcoded plan matrix in the template).
- `GET /profile/data/` (Phase 1, extended) — `accountType` is now the real
  resolved plan (was a hardcoded `"Free"` literal), plus a nested
  `entitlement` summary object.

## Test strategy (Step 26)

235 tests total (233 Phase-1-plus-Phase-2 + 2 PostgreSQL-gated concurrency
tests that self-skip on SQLite), across:

- `test_entitlement_engine.py` — Plan/Feature/PlanFeatureRule model
  invariants, guest/free/premium resolution, expiration boundaries, admin
  overrides + audit log.
- `test_entitlement_security.py` — spoofing (plan/feature/user via body,
  query, cookie, header), cross-user isolation, public-catalog PK leakage.
- `test_entitlement_concurrency.py` — PostgreSQL-gated, mirrors
  `test_credit_concurrency.py`'s exact pattern; **not meaningfully
  verified on SQLite** (see PHASE_2_COMPLETION_AUDIT.md).
- `test_ai_entitlement_integration.py` — entitlement/quota denial blocks
  credit reservation and the provider call entirely (asserts
  `mock_post.assert_not_called()` and an unchanged `CreditAccount`
  balance); an allowed request still runs the full Phase 1 credit flow
  unchanged; expired Premium falls back to FREE rather than blocking;
  successful requests record quota usage, failed provider calls don't.

## Future subscription/payment compatibility (Step 32)

Phase 3/4 add a `Subscription`/`Payment` layer that creates
`UserEntitlement` rows the same way `entitlement_admin.grant_entitlement()`
does today — the conceptual chain becomes:

```
Payment -> Subscription -> UserEntitlement -> Plan -> PlanFeatureRule -> Feature
```

Nothing about `Plan`, `Feature`, `PlanFeatureRule`, `UserEntitlement`, or
`AccessDecision` needs to change for that: `UserEntitlement.source` already
exists as a free-text provenance field (today it's always
`"admin_override"` or a test-fixture marker; Phase 3 would instead write
`f"subscription:{subscription.id}"` there, or Phase 3 could add a
`subscription = ForeignKey(Subscription, null=True)` column via a normal
migration without touching any existing column). `expires_at` is already
exactly what a subscription renewal/cancellation would set. No rewrite —
an additive migration and a second caller of the same grant/revoke
functions in `entitlement_admin.py` (or their Phase-3 equivalents) is the
expected shape of that work.
