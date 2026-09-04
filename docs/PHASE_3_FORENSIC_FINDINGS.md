# PHASE 3 — FORENSIC FINDINGS

Before writing any Phase 3 code, the existing Phase 2 codebase was inspected in
full. This document records what was found and how Phase 3 was designed to
sit on top of it without duplicating or weakening it.

## 1. How Phase 2 currently resolves GUEST / FREE / PREMIUM

All resolution lives in `accounts/services/entitlement_service.py`, specifically
`_resolve_identity(user, guest_key)`:

- **GUEST**: `user is None` → returns the virtual `GUEST_PLAN_CODE` (from
  `accounts/entitlement_config.py`). No `Plan` row exists for GUEST and no DB
  record is ever created for a guest — this is a pure code-level policy branch.
- **FREE**: `user` is set but `_get_active_entitlement(user)` returns `None`
  (no ACTIVE, time-window-valid `UserEntitlement` row) → the function returns
  `Plan.Code.FREE` as an implicit default. **No `UserEntitlement` row is ever
  created for FREE** — it is purely the absence of a Premium grant.
- **PREMIUM**: `user` has a `UserEntitlement` row with `status=ACTIVE` whose
  `effective_status()` (a computed property, never a stored/trusted field
  alone) evaluates to `"ACTIVE"` at the current time. `effective_status()`
  checks `status`, `starts_at`, and `expires_at` together — expiry is always
  timestamp-derived, never solely a separately-updated status flag.

`get_current_plan()` is the single public entrypoint every other part of the
app (AI endpoints, `/api/entitlements/`, `/profile/data/`) goes through. Phase
3 does not add a second path to plan resolution — it only ever *feeds*
`UserEntitlement` through the existing `entitlement_admin.py` functions so
that this same resolution keeps working unchanged.

## 2. How UserEntitlement is created/revoked today

The only code path allowed to create or mutate `UserEntitlement` is
`accounts/services/entitlement_admin.py`:

- `grant_entitlement(user, plan, performed_by, reason, expires_at, source)` —
  creates a new ACTIVE row, or **extends the existing ACTIVE row's
  `expires_at`** if one already exists for that `(user, plan)` pair (this is
  deliberate, not accidental — the DB's
  `uq_userentitlement_one_active_per_user_plan` constraint would reject a
  second ACTIVE row anyway).
- `revoke_entitlement`, `suspend_entitlement`, `reactivate_entitlement` — flip
  `status` and always write an `EntitlementAuditLog` row.

Nothing else in the codebase touches `UserEntitlement.status` /
`expires_at` directly. `accounts/admin.py`'s `UserEntitlementAdmin` calls into
these same functions from bulk actions rather than allowing raw field edits
outside of the initial creation form.

**Phase 3 conclusion**: `subscription_service.py` must call
`entitlement_admin.grant_entitlement` / `revoke_entitlement` — never touch
`UserEntitlement` fields directly, and never re-implement the "one ACTIVE row
per (user, plan)" rule (the DB already enforces it).

## 3. Existing `source` field convention

`UserEntitlement.source` is a free-text audit trail (e.g.
`"admin_override:<staff_username>"`, `"test_fixture"`). Its docstring
literally anticipates Phase 3:

> "Never a payment reference in Phase 2 — see PHASE_2_ENTITLEMENT_ENGINE.md
> 'Future payment compatibility' for how Phase 3/4 will populate this instead
> via a Subscription FK once that model exists."

Phase 3 follows this exactly: every entitlement created by
`subscription_service.activate_subscription()` is granted with
`source=f"subscription:{subscription.pk}"`.

## 4. Existing conventions Phase 3 reuses as-is

- **Service-layer ownership**: exactly one service module owns each model's
  mutations (`credit_service.py` → `CreditAccount`, `entitlement_admin.py` →
  `UserEntitlement`). Phase 3 adds `subscription_service.py` →
  `Subscription`, following the same rule.
- **Append-only audit log per domain**: `CreditTransaction`,
  `EntitlementAuditLog`. Phase 3 adds `SubscriptionAuditLog`, same shape
  (`has_delete_permission` / `has_change_permission` both `False` in admin).
- **`transaction.atomic()` + `select_for_update()`** for every state-changing
  operation, with a documented note (in `pg_test_settings.py` and the
  concurrency test files) that SQLite does not meaningfully enforce row
  locking — concurrency claims are only trusted against a real PostgreSQL
  run.
- **Fail-closed design**: entitlement checks return "not entitled" on
  anything unrecognized rather than defaulting to allow. Phase 3's state
  machine mirrors this — `VALID_TRANSITIONS` is a closed allow-list; anything
  not explicitly listed raises `InvalidTransitionError`.
- **No client-facing "become Premium" endpoint** exists anywhere in Phase 1/2.
  Phase 3 preserves this invariant exactly — see
  `PHASE_3_SUBSCRIPTION_ARCHITECTURE.md` §6.

## 5. Files inspected

`accounts/models.py`, `accounts/services/entitlement_service.py`,
`accounts/services/entitlement_admin.py`, `accounts/entitlement_config.py`,
`accounts/entitlement_views.py`, `accounts/admin.py`, `accounts/urls.py`,
`accounts/views.py` (`profile_data`), `AI_GENERATORS/settings.py`,
`pg_test_settings.py`, all files under `accounts/migrations/`, and the
existing test suite under `accounts/tests/` (in particular
`test_entitlement_engine.py`, `test_entitlement_security.py`,
`test_entitlement_concurrency.py` for established test conventions).

## 6. A bug this work surfaced in existing Phase 2 test infrastructure

`accounts/tests/test_entitlement_concurrency.py`'s `setUp()` used
`Plan.objects.get(code="PREMIUM")`, silently depending on migration-seeded
catalog data still being present in the test database. `TransactionTestCase`
truncates **all** tables after each test unless `serialized_rollback=True` is
set, and Django intentionally runs all `TransactionTestCase`-based tests
*after* all `TestCase`-based ones specifically to avoid this — but multiple
`TransactionTestCase` classes among themselves are not protected from each
other. Adding Phase 3's own `TransactionTestCase`-based concurrency tests
exposed this: depending on discovery/run order, an earlier
`TransactionTestCase` could flush the `Plan` table before a later one's
`setUp()` ran, causing a spurious `Plan.DoesNotExist`.

Fixed with a one-line, behavior-neutral change (`get_or_create` instead of
`get`) in both `test_entitlement_concurrency.py` and Phase 3's own
`test_subscription_concurrency.py` — no Phase 2 production code was touched.
See `PHASE_3_COMPLETION_AUDIT.md` for the verification run that surfaced
this.
