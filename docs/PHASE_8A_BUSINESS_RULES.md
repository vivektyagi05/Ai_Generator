# PHASE 8A BUSINESS RULES

Describes the actual rules enforced by the code as it exists after
Items 1-9 (see PHASE_8A_PROGRESS.md for the full trace/evidence behind
each entry), not an idealized design. Each rule names its source of
truth, where it's enforced, and the test(s) that pin it down.

## Auth → Entitlement → Credit → Provider gate (the canonical order)

```
REQUEST
  → AUTHENTICATION        (is there a real request.user?)
  → FEATURE/ENTITLEMENT   (entitlement_service.get_access_decision)
  → CREDIT ELIGIBILITY    (credit_service.can_afford, inside reserve())
  → ATOMIC RESERVATION    (credit_service.reserve(), select_for_update)
  → PROVIDER CALL         (requests.post to Groq / image provider)
  → SUCCESS → finalize()  |  FAILURE → refund()
```

- SOURCE OF TRUTH: `accounts/services/entitlement_service.py`
  (`get_access_decision`/`_decide`) for WHETHER a request may proceed;
  `accounts/services/credit_service.py` for WHETHER it can be paid for.
- ENFORCEMENT LOCATION: `AI_GENERATORS/api_views.py:ai_generate()`,
  server-side only. Frontend (`templates/main.html`'s
  `runGeneratorLifecycle()`) reflects this state for UX but is never
  itself a gate — every check it performs is re-checked server-side.
- TEST: `test_ai_entitlement_integration.py`,
  `test_ai_credit_integration.py`,
  `test_phase8a_generator_reverification.py`.

## Anonymous visitors cannot generate (Phase 8A canonical rule — supersedes the prior guest-quota design)

- RULE: an unauthenticated request to any of the 5 generators is denied
  with `401 AUTHENTICATION_REQUIRED` before any `CreditAccount` is
  created, before any credit is granted or reserved, and before the
  provider is ever called.
- SOURCE OF TRUTH: `entitlement_service._decide()`'s guest branch
  (`plan_code == GUEST_PLAN_CODE`).
- OLD RULE (retired this phase): a guest could generate up to
  `GUEST_DAILY_LIMIT` requests/day/feature, silently creating a guest
  `CreditAccount`, granting it `INITIAL_FREE_CREDITS` (50) on first
  touch, and spending against it — this is what produced the reported
  "0 → 45" symptom. See PHASE_8A_PROGRESS.md Item 2 for the full A–L
  trace proving this, and PHASE_8A_CREDIT_ENTITLEMENT_AUDIT.md for the
  test-by-test old-rule/new-rule table.
- FRONTEND BEHAVIOR: `templates/main.html`'s `AUTHENTICATION_REQUIRED`
  branch (already existed, previously unreachable) shows the
  login/signup action.
- BACKEND BEHAVIOR: `_check_access_policy()` short-circuits with 401.
- TEST: `test_entitlement_engine.py`, `test_ai_entitlement_integration.py`,
  `test_ai_credit_integration.py`.

## Credit reservation, finalization, refund

- WHEN CREDIT IS CHECKED: `credit_service.reserve()`, via
  `can_afford()`, inside a `select_for_update()`-locked transaction.
- WHEN CREDIT IS RESERVED: same call, atomically with the check — no
  window between "checked" and "reserved" a second request could race
  through (the DB-level `uq_credittxn_account_reference_type`
  constraint is the actual race boundary, not the lock alone; see
  below).
- WHEN CREDIT IS CONSUMED: `ai_usage_service.complete_success()` →
  `credit_service.finalize()`, only by the caller that won the
  reservation for that `request_id`.
- WHEN CREDIT IS REFUNDED: `ai_usage_service.complete_failure()` →
  `credit_service.refund()`, on provider failure/timeout/error.
- SOURCE OF TRUTH: `credit_service.py` — the single, only credit
  ledger implementation in the project (verified: no `credit_service_v2`
  or parallel calculator exists anywhere).
- TEST: `test_credit_concurrency.py`,
  `test_ai_idempotency_conflict.py` (including the Item 5 race-condition
  addition).

## Same-request_id race (Phase 8A fix)

- RULE: two genuinely concurrent submissions of the *same*
  `request_id` must both resolve gracefully — one succeeds (or is
  already in flight), the other gets the same `409` idempotent-replay
  response every later duplicate already gets, never an unhandled 500.
- SOURCE OF TRUTH: the DB-level unique constraint on
  `(account, reference_id, transaction_type)` is what actually decides
  the winner; `AI_GENERATORS/api_views.py:ai_generate()`'s
  `DuplicateRequestError` handler translates the loser's failure into
  the standard response.
- TEST: `test_ai_idempotency_conflict.py::GenuinelyConcurrentSameRequestIdTests`.

## Forgot-password / OTP

- WHO IS ALLOWED: only an email matching a real `User` row can ever
  receive a usable OTP. `forgot_send_otp` checks existence before
  calling `issue_otp()`; a nonexistent email gets the same generic
  response with no OTP ever created.
- ENUMERATION RESISTANCE: identical response body from `forgot_send_otp`
  for registered/unregistered emails (pre-existing), AND (Phase 8A fix)
  identical response from `forgot_verify_otp` whether the email never
  had an OTP or a real pending OTP's code was guessed wrong.
- SINGLE USE / EXPIRY / ATTEMPT LIMIT: enforced in
  `otp_service.verify_otp()` — record deleted on success, on expiry,
  and on exceeding `MAX_OTP_ATTEMPTS`.
- SOURCE OF TRUTH: `accounts/otp_service.py` (shared by both the
  reset and signup flows — one implementation, not two).
- TEST: `test_forgot_password.py`, `test_otp_service.py`.

## No security-relevant decision is frontend-only

Verified across every mutating endpoint (`accounts/views.py`,
`accounts/billing_views.py`, `accounts/entitlement_views.py`): each is
either `@require_POST`-decorated or does an internal
`request.method != "POST"` → `405` check. No GET request mutates
state anywhere in the project (see PHASE_8A_PROGRESS.md Item 7).

## Ambiguities encountered and the choice made

- **"Failure/refund behavior" and "retry behavior" for the 4
  newly-re-gated generators**: no ambiguity was actually found — these
  paths are shared infrastructure (`ai_usage_service.complete_failure`/
  `runGeneratorLifecycle`'s retry button) that every generator already
  used identically before this phase; re-gating guests didn't change
  authenticated-user behavior on these paths at all.
- **Guest session identity vs. AI-generation authorization**: the
  master prompt explicitly required keeping these separate. Guest
  session infra (`accounts/services/guest_identity.py`) was left
  untouched; only the entitlement decision for *generation* was
  changed. `migrate_guest_credits_to_user()` (for any guest balance
  that predates this fix) was likewise left in place, not deleted.
