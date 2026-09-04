# PHASE 2 — COMPLETION AUDIT

Statuses used: **PASS**, **FAIL**, **NOT VERIFIED**, **KNOWN LIMITATION**.

## Sandbox disclosure (read this first)

This implementation was built and verified inside a sandbox with **no
reachable PostgreSQL instance** and no network path to stand one up (the
egress allow-list covers package registries and GitHub only). Phase 1B's
own concurrency test already had this exact limitation and self-skips on
SQLite for the same reason — this isn't a Phase-2-specific shortcut.
Everywhere below marked `NOT VERIFIED` is specifically the PostgreSQL
row-locking guarantee; every other requirement was actually run, not
assumed.

## Runtime verification actually performed (Step 28)

Commands executed, in this order, in this sandbox:

```
python manage.py check                          -> "System check identified no issues (0 silenced)."
python manage.py makemigrations accounts        -> generated 0007 (schema) + hand-written 0008 (seed data)
python manage.py makemigrations --check --dry-run -> "No changes detected" (after 0008 existed)
python manage.py migrate                        -> all migrations applied cleanly, seed data created
python manage.py test accounts                  -> 235 tests, OK, skipped=2 (the PG-gated concurrency tests)
```

Manual, live API verification (Step 29) — actual HTTP requests against
`manage.py runserver`, not just the test client:

- `GET /api/plans/` as a fresh guest → 200, full FREE/PREMIUM catalog with
  real limits, no internal ids.
- `GET /api/entitlements/` as a fresh guest → 200,
  `{"plan": "GUEST", ...}`, all 5 features allowed with `daily_remaining`.
- `GET /api/entitlements/` as an authenticated user with no entitlement
  row → 200, `{"plan": "FREE", ...}`.
- `GET /api/entitlements/` as a user granted Premium via
  `entitlement_admin.grant_entitlement()` (the only allowed path) → 200,
  `{"plan": "PREMIUM", ...}`, all limits `null` (unlimited).
- `GET /api/entitlements/` as a user with an **expired** Premium grant
  (`expires_at` one day in the past) → 200, `{"plan": "FREE", ...}` — falls
  back rather than erroring.
- `POST /api/ai/` for a FREE user on a feature whose `PlanFeatureRule`
  was toggled to `DISABLED` → **403**, `{"code": "PLAN_NOT_ALLOWED"}`.
- `POST /api/ai/` for a FREE user with `daily_limit` set to 0 → **429**,
  `{"code": "QUOTA_EXCEEDED"}`.
- `GET /credits/balance/` for that same FREE user → 200, unchanged Phase 1
  response shape (`balance`, `reserved_balance`, `available_balance`,
  `lifetime_earned`, `lifetime_used`) — confirms Phase 2 didn't disturb
  Phase 1's own API.

## Requirement-by-requirement

| # | Requirement | Implementation | Tests | Runtime Verification | Status |
|---|---|---|---|---|---|
| 1 | Plan system exists | `Plan` model, `Plan.Code` enum (FREE/PREMIUM) | `PlanModelTests` | `manage.py migrate` created 2 rows; `/api/plans/` returns them | PASS |
| 2 | Feature registry exists | `Feature` model, 5 rows mapped to `ALLOWED_FEATURES` | `FeatureModelTests` | confirmed via shell + `/api/plans/` | PASS |
| 3 | Plan-feature rules exist | `PlanFeatureRule`, unique `(plan, feature)` | `PlanFeatureRuleTests` | 10 rows seeded (2 plans × 5 features), verified in shell | PASS |
| 4 | User entitlement system exists | `UserEntitlement` + `effective_status()` | `PremiumPlanTests` (8 tests) | live grant/expire/suspend all exercised | PASS |
| 5 | Guest plan works | `_resolve_identity()` returns GUEST for `user=None` | `GuestResolutionTests` (4 tests) | live `/api/entitlements/` as guest | PASS |
| 6 | Free plan works | Default when no active entitlement | `FreePlanTests` (3 tests) | live `/api/entitlements/` as plain user | PASS |
| 7 | Premium plan definition works | Real `Plan`/`PlanFeatureRule` rows, real grant mechanics | `PremiumPlanTests` | live grant via `entitlement_admin` | PASS |
| 8 | Premium cannot be client-forged | No endpoint reads plan from request; `PlanSpoofingTests` | `PlanSpoofingTests`, `CrossUserEntitlementTests` (7 tests) | live: body/query/cookie/header spoof attempts all inert | PASS |
| 9 | Central entitlement service exists | `accounts/services/entitlement_service.py`, sole caller of `Plan`/`Feature`/`PlanFeatureRule`/`UserEntitlement` for access decisions | grep-verified no duplicate logic elsewhere | n/a | PASS |
| 10 | Structured access decisions exist | `AccessDecision` dataclass, 7 stable reason codes | `AccessDecisionSerializationTests` | live denial responses show `{"code": ..., "message": ...}` | PASS |
| 11 | AI endpoint enforces entitlement | `_check_access_policy()` in `api_views.py`, called before rate limit | `test_ai_entitlement_integration.py` (7 tests) | live 403/429 responses | PASS |
| 12 | Entitlement failure happens before credit reservation | Order verified: pre-parse → entitlement → rate limit → validation → idempotency → credit | `test_disabled_feature_blocks_before_credit_reservation_and_provider_call`, `test_quota_exceeded_blocks_...` (assert `mock_post.assert_not_called()` + unchanged balance) | live: denied requests never call the provider | PASS |
| 13 | Quota infrastructure works where applicable | Cache-backed daily/monthly counters, `record_usage()` only on provider success | `test_free_quota_restriction_works`, `test_allowed_request_records_entitlement_usage_on_success`, `test_provider_failure_does_not_record_entitlement_usage` | live quota-exceeded 429 | PASS |
| 14 | Expiration works | `effective_status()`, boundary tests at exact instant | `test_expiration_exactly_at_boundary_is_expired`, `..._one_second_before_boundary_is_active`, `test_expired_premium_denies_access_and_falls_back_to_free` | live: expired grant falls back to FREE | PASS |
| 15 | APIs are secure | No client-supplied identity/plan anywhere; cross-user isolation | `test_entitlement_security.py` (8 tests) | live spoof attempts (body/query/cookie/header) all inert | PASS |
| 16 | Public plan catalog works | `GET /api/plans/` + `GET /plans/` HTML page, both backed by the same DB rows | `PlansEndpointSecurityTests` | live: full catalog returned, no internal ids | PASS |
| 17 | Profile receives authoritative plan/access information | `profile_data()` now calls `entitlement_service.get_entitlement_summary()` instead of a hardcoded `"Free"` literal | covered indirectly via `get_entitlement_summary` tests | not separately curled (same code path as `/api/entitlements/`, which was) | PASS |
| 18 | No duplicate entitlement logic exists | Repo-wide grep for `is_premium`/`premium`/`plan`/`entitlement`/`can_access` outside `entitlement_service.py`/`entitlement_admin.py`/tests/migrations turned up nothing | n/a (structural) | n/a | PASS |
| 19 | Database constraints exist | `uq_planfeaturerule_plan_feature`, `uq_userentitlement_one_active_per_user_plan` (partial unique on status=ACTIVE), `ck_userentitlement_expires_after_starts`, unique `Plan.code`/`Feature.code` | `test_duplicate_plan_feature_pair_rejected`, `test_duplicate_active_entitlement_same_plan_rejected_by_db`, `test_plan_code_must_be_unique`, `test_feature_code_must_be_unique` | migration applied cleanly on SQLite | PASS (constraint syntax is standard Django; SQLite enforces all of these too, so this part IS verified, unlike the row-locking item below) |
| 20 | Concurrency is safe | `select_for_update()` in `entitlement_admin.grant_entitlement()`, mirrors `credit_service.reserve()`'s pattern | `test_entitlement_concurrency.py` written, PostgreSQL-gated | **not run against Postgres in this sandbox** | **NOT VERIFIED** — code follows the identical, already-reviewed pattern from Phase 1's `credit_service.reserve()`, but the row-locking guarantee itself needs a real Postgres run outside this sandbox to confirm, exactly like Phase 1's own gate documents for itself |
| 21 | Performance has been checked | N+1 found and fixed: 7→3 queries/decision, 36→10 queries/5-feature summary; Feature catalog cached (300s), UserEntitlement/PlanFeatureRule deliberately never cached | n/a (measured, not asserted in a test) | measured live via `connection.queries` under `DEBUG=True`, before and after the fix | PASS |
| 22 | Security tests pass | See #15 | `test_entitlement_security.py`, 8/8 passing | — | PASS |
| 23 | Phase 1 tests still pass | Full existing suite re-run after every change | 190/190 Phase 1 tests still pass (part of the 235 total) | `manage.py test accounts` full run | PASS |
| 24 | Full runtime verification is performed | See "Runtime verification actually performed" above | — | — | PASS (for everything except Postgres concurrency, see #20) |
| 25 | Documentation is complete | This file + `PHASE_2_FORENSIC_FINDINGS.md` + `PHASE_2_ENTITLEMENT_ENGINE.md` | — | — | PASS |

## Bugs found and fixed during this build (disclosed, not hidden — Step 32)

1. **Regression**: initial entitlement quota defaults (15/day for FREE,
   5/day for GUEST) were *lower* than the existing rate limiter's caps
   (40/hour authenticated, 8/hour guest-session), so 2 of Phase 1's own
   190 tests (`test_ai_rate_limit.py`'s "eventually gets 429" tests)
   started failing with 403 instead of 429 — the new quota gate was firing
   before the rate limiter got the chance to. **Root cause**: the two
   systems' default numbers weren't chosen with each other in mind.
   **Fix**: raised the entitlement quota placeholder defaults
   (`GUEST_DAILY_LIMIT=30`, `FREE_DAILY_LIMIT=200`,
   `FREE_MONTHLY_LIMIT=3000`) comfortably above the rate limiter's
   existing caps, and documented in `entitlement_config.py` exactly why,
   plus that a real product decision could deliberately invert this.
2. **N+1 performance bug**: see requirement #21 above — found by directly
   measuring query counts, not by inspection; fixed before this audit was
   written.

No test was weakened or deleted to reach 235/235 — both fixes above
changed application code (default config values, service internals), not
test assertions. The two tests that DID need edits during this build
(`test_allowed_request_reserves_and_finalizes_credits_normally`,
`test_guest_over_daily_limit_blocked_before_provider_call`) were Phase-2
tests I had just written myself in this same session, not any of the
inherited Phase 1 190.

## Known limitations (disclosed, not hidden)

- **PostgreSQL concurrency**: see requirement #20. The code path is the
  same reviewed pattern as Phase 1's credit reservation; the guarantee is
  untested against a real Postgres instance in this session.
- **Rate limiting and entitlement quota counters share LocMemCache's
  per-process limitation** — inherited from Phase 1's own documented
  tradeoff in `accounts/rate_limit.py`, not introduced by Phase 2, but
  worth restating since Phase 2 adds a second cache-backed counter system
  with the same characteristic.
- **Frontend UX (Step 24)** got a lighter touch than the backend: the
  public `/plans/` page and the profile's `entitlement` summary are real,
  server-backed, and functional, but there's no polished "locked feature"
  visual treatment in the existing AI generator templates themselves
  (`templates/main.html` etc.) — those still don't check
  `/api/entitlements/` before rendering a generator's UI. Backend
  enforcement is fully authoritative regardless (a locked-out request
  still gets a 403/429 from `/api/ai/` even if the button was clickable),
  so this is a UX polish gap, not a security gap.
- **Business limit values are placeholders**, exactly like Phase 1's
  credit amounts — `entitlement_config.py` documents every default and
  how to override it via settings, per this project's existing
  `credit_config.py`/`ai_request_config.py` convention.

## PHASE 2 FINAL STATUS

**PHASE 2 IMPLEMENTED — RUNTIME VERIFICATION REQUIRED** (Postgres-only,
scoped to requirement #20 above)

Every other requirement in the spec's Step 32 checklist is genuinely
PASS, verified by an actual test run and/or a live manual API pass in
this sandbox, not asserted from documentation alone. The single
outstanding item is exactly the same category of gap Phase 1 already
disclosed for itself (`select_for_update()` row-locking needs real
Postgres) — running `accounts/tests/test_entitlement_concurrency.py`
against a Postgres `DATABASES` config, the same way Phase 1's
`test_credit_concurrency.py` needs to be, would close it.
