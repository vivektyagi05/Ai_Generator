# PHASE 2 — FORENSIC FINDINGS

Reviewed before any Phase 2 code was written. Source: `AI_GENERATORS_PHASE1B_VERIFIED.zip`
(folder root: `AI_GENERATORS_EMAIL_FIXED/`).

## 1. Current user identity model
Django's built-in `auth.User`. No custom user model, no roles/groups used
anywhere in `accounts/models.py`. Authentication is session-based
(`django.contrib.sessions`), OTP-verified signup/login in `accounts/views.py`.

## 2. Current guest identity model
`accounts/services/guest_identity.py::identify_requester(request)` returns
`(user, None)` for an authenticated request or `(None, guest_key)` for an
anonymous one, where `guest_key` is Django's server-issued
`request.session.session_key` (forced-created via `request.session.save()`
if absent). Never client-supplied. This is the identity primitive Phase 2
reuses unchanged.

## 3. Current CreditAccount architecture
`accounts/models.py::CreditAccount` — one row per identity, enforced by a
`CheckConstraint` requiring exactly one of `user` / `guest_session_key`
(`ck_creditaccount_exactly_one_owner`). All mutation goes through
`accounts/services/credit_service.py` (`grant`, `reserve`, `finalize`,
`refund`), which locks the row with `select_for_update()` inside
`transaction.atomic()` and writes an immutable `CreditTransaction` ledger
row for every mutation. `reference_id` + `transaction_type` has a DB-level
uniqueness constraint (`uq_credittxn_account_reference_type`) that backstops
idempotency even under a race.

## 4. Current AI request lifecycle
`AI_GENERATORS/api_views.py::gemini_api` (bound at `/api/ai/`):
method check → CSRF (via `CsrfViewMiddleware`, not exempted) → identity
resolution → `_check_access_policy()` (currently a **no-op stub that always
returns `None`** — explicitly documented as "Premium not implemented, out of
scope for Phase 1A") → rate limit (`accounts/rate_limit.py::check_ai_rate_limit`)
→ request validation (`accounts/ai_request_config.py`) → idempotency
(`accounts/services/ai_usage_service.py::check_existing_usage`) → credit
reservation (`ai_usage_service.begin_request` → `credit_service.reserve`) →
provider call (Groq) → finalize/refund (`ai_usage_service.complete_success`
/ `complete_failure`) → response.

`_check_access_policy(user, guest_key)` is the exact seam Phase 2 must fill —
it already sits before rate-limiting and reservation, which is where an
entitlement check belongs per Step 11 of the Phase 2 spec.

## 5. Current rate-limit lifecycle
`accounts/rate_limit.py::check_ai_rate_limit` — three independent counters
via Django's cache framework (`LocMemCache` by default, documented as
per-process/non-airtight): per-authenticated-user, per-guest-session,
per-guest-IP. Comment explicitly notes: *"Premium" has no entitlement system
yet ... shares `AI_AUTH_MAX_PER_WINDOW` with authenticated-free for now.*
Phase 2 does not change these numbers or this module — entitlement is a
separate, earlier gate (per Step 10/11 of the spec), not a replacement for
rate limiting.

## 6. Current feature names
`accounts/ai_request_config.py::ALLOWED_FEATURES` (overridable via
`AI_ALLOWED_FEATURES`), default: `chat`, `code`, `story`, `application`,
`proposal`. `AIUsage.feature` stores exactly one of these slugs. These are
the only real, existing generator features — Phase 2's `Feature` registry
codes (`AI_CHAT`, `AI_CODE`, `AI_STORY`, `AI_APPLICATION`, `AI_PROPOSAL`) map
1:1 onto them, nothing invented.

## 7. Current API endpoints
- `POST /api/ai/` — the single AI generation endpoint (all 5 features
  multiplex through it via the `feature` field).
- `GET /credits/balance/` — read-only credit balance (`accounts/views.py::credit_balance`).
- `GET /profile/data/` — profile JSON, currently **hardcodes**
  `"accountType": "Free"` (accounts/views.py:465) — this is the one place in
  the repo that already gestures at a "plan" concept, and it's fake. Phase 2
  replaces this literal with the real resolved plan code.
- Auth/OTP/history/profile CRUD endpoints — untouched by Phase 2.

## 8. Current profile/dashboard architecture
`templates/profile.html` renders from `GET /profile/data/`, a plain
`JsonResponse` built in `accounts/views.py::profile_data`. No plan/limits
data is served today.

## 9. Existing admin architecture
`accounts/admin.py` registers `CreditAccount`, `CreditTransaction` (ledger
rows made read-only/non-deletable via `has_change_permission`/
`has_delete_permission` returning `False` — a pattern Phase 2 mirrors for
`EntitlementAuditLog`), and `AIUsage`.

## 10. Existing plan/subscription/role fields
**None.** Confirmed by repo-wide search for `premium`, `is_premium`, `plan`
outside `/migrations/`: the only hits are Phase 1 code comments explicitly
stating Premium is out of scope, plus two unrelated matches inside
generated document text in `templates/main.html` ("Implementation Plan" —
prose, not code). There is no `is_premium` flag, no `Plan` model, no role
field on `User`/`UserProfile` anywhere. This is a genuinely greenfield
Phase 2 — nothing to migrate away from, nothing to reconcile.

## Sandbox constraint affecting verification (disclosed up front)
This environment has no reachable PostgreSQL instance and no network access
to stand one up (egress allow-list is limited to package registries and
GitHub). Phase 1B's own concurrency test
(`accounts/tests/test_credit_concurrency.py`) already self-skips on SQLite
for exactly this reason (`select_for_update()` isn't meaningfully enforced
there) and documents that it must be run against real Postgres separately.
Phase 2's new concurrency test for `UserEntitlement` follows the identical
pattern and has the identical limitation: it will run and pass its
SQLite-safe assertions here, but the row-locking guarantee itself is
**NOT VERIFIED in this session** — flagged honestly in
`PHASE_2_COMPLETION_AUDIT.md` rather than claimed.
