# PHASE 8A TEST AUDIT

## Final numbers (this checkpoint)

```
python manage.py check                          -> System check identified no issues (0 silenced)
python manage.py makemigrations --check --dry-run -> No changes detected
python manage.py test accounts                  -> Ran 792 tests in ~142s, OK (skipped=9)
```

The 9 skips pre-date this phase (unrelated environment-gated cases,
e.g. Postgres-only concurrency tests skipped under SQLite) and were not
introduced or modified this phase.

## New test files added this phase

- `accounts/tests/test_phase8_nav_duplicate_panel_fix.py` (Item 1)
- `accounts/tests/test_phase8a_generator_reverification.py` (Item 3)

## Existing test files extended this phase (reused, not duplicated)

- `accounts/tests/test_entitlement_engine.py` (Item 2)
- `accounts/tests/test_ai_entitlement_integration.py` (Item 2)
- `accounts/tests/test_ai_credit_integration.py` (Item 2)
- `accounts/tests/test_ai_rate_limit.py` (Item 2)
- `accounts/tests/test_ai_request_validation.py` (Item 2)
- `accounts/tests/test_ai_csrf.py` (Item 2)
- `accounts/tests/test_forgot_password.py` (Item 4)
- `accounts/tests/test_ai_idempotency_conflict.py` (Item 5)
- `accounts/tests/test_no_dead_ui.py` (Item 6)

## Discipline followed (per the master prompt's testing-discipline section)

Every fix in Items 1, 2, 4, 5, and 6 followed: reproduce (either
directly at the service layer or via a real `Client()` HTTP call) →
failing regression test or direct reproduction script → root-cause fix
→ focused test run → related-suite run → full-suite run. Items 3, 7,
8, 9 were audit-only findings of "already correct" — no fix, so no new
failing-test-first cycle applied, but each was verified by tracing real
code/running real requests, not by re-trusting a prior phase's
documentation.

Six retired test assertions (Item 2's guest-quota tests, listed in
full in PHASE_8A_CREDIT_ENTITLEMENT_AUDIT.md's table) were replaced,
never silently weakened — each replacement is documented in its own
test file with OLD RULE / NEW CANONICAL RULE / WHY THE OLD ASSERTION
WAS WRONG / WHICH TEST WAS UPDATED, per the master prompt's testing
discipline requirement.

## What full-suite coverage does and doesn't prove

The full suite exercises real Django `Client()` requests against real
views, real DB transactions (including `select_for_update` locking
paths, `TransactionTestCase` for the Postgres-gated concurrency tests),
and real template rendering. It does not execute browser JavaScript —
no click-driven, real-DOM test of `runGeneratorLifecycle()` or the nav
mobile-panel toggle exists in this environment. Frontend JS correctness
was established instead by: (a) direct static tracing of every
DOM id/function reference against the rendered template output
(Items 1, 3, 6), and (b) exercising the real HTTP contract those JS
functions call into (Items 2, 3, 5), which is what a passing/failing
fetch() response would actually look like from the browser's
perspective.
