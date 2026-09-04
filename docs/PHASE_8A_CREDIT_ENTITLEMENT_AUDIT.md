# PHASE 8A CREDIT & ENTITLEMENT AUDIT

Full A–L reproduction trace and root-cause proof for the 0→45 bug is in
PHASE_8A_PROGRESS.md Item 2 — not duplicated here. This doc is the
audit summary: what changed, why, and the retired-vs-new test table.

## What changed

`accounts/services/entitlement_service.py`'s `_decide()` guest branch:
was quota-gated (`allowed=True` up to `GUEST_DAILY_LIMIT`/feature/day),
now unconditionally `allowed=False, reason=AUTHENTICATION_REQUIRED`.

## Why (business decision, not a code defect being "discovered")

The guest-quota design was working as built — it was a real, deliberate
product decision from an earlier phase, evidenced by its own dedicated
tests (`test_phase8_guest_credit_migration.py`) and its own frontend
copy. It produced the 0→45 symptom precisely *because* it worked as
designed: real grant, real charge, real ledger entry, just for an
identity (guest) the product owner has now decided should never be able
to spend real AI-generation capacity at all.

## Retired-rule / new-rule test table

| File | Retired assertion | New assertion |
|---|---|---|
| `test_entitlement_engine.py` | guest allowed under daily quota; denied only past it (`GUEST_RESTRICTION`) | guest denied unconditionally (`AUTHENTICATION_REQUIRED`), independent of usage count |
| `test_ai_entitlement_integration.py` | guest's first request succeeds (200); denied only after quota filled (403 `GUEST_RESTRICTION`) | guest denied (401) on the very first request; still denied even with quota patched to 1000 |
| `test_ai_credit_integration.py` | guest can use the free initial grant (200, real `AIUsage` row) | guest cannot reach provider or ledger (401, zero `CreditAccount` rows created) |
| `test_ai_rate_limit.py` | guest rate limiter (8/hr) eventually returns 429 after N real requests | guest denied (401) on every attempt — rate limiter is unreachable dead-code from this endpoint now, left in place not deleted |
| `test_ai_request_validation.py` | ran unauthenticated | now authenticated (this file tests payload validation, not guest policy) |
| `test_ai_csrf.py` | `test_valid_csrf_token_is_accepted` asserted 200 as a guest | now authenticated (this file tests CSRF, not guest policy) |

Every retired assertion encoded the pre-8A guest-credit design — a
product decision explicitly reversed this phase, not a test bug. No
assertion was weakened; each replacement proves the new, stricter
boundary with equal or greater precision.

## Race-condition finding (Item 5)

Separate from the guest-policy change: a genuinely concurrent
duplicate submission of the *same* `request_id` could raise an
uncaught `DuplicateRequestError` (unhandled 500) instead of the
standard `409` idempotent-replay response. Fixed in
`AI_GENERATORS/api_views.py:ai_generate()` by catching it and routing
to the existing `_replay_existing()` path. See
PHASE_8A_PROGRESS.md Item 5 for the full reproduction (both at the
service layer and the HTTP layer) and
`test_ai_idempotency_conflict.py::GenuinelyConcurrentSameRequestIdTests`.

## What was checked and found already correct (no change needed)

- `reserve()`/`finalize()`/`refund()`/`grant()`: all correctly use
  `select_for_update()` and the DB-level unique constraint; no
  double-reservation or double-charge path found.
- `migrate_guest_credits_to_user()`: idempotent, one-directional
  (guest → user only), left untouched — still correct and still needed
  for any guest balance created before this fix shipped.
- No `credit_service_v2` or parallel credit calculator exists anywhere
  in the project.
