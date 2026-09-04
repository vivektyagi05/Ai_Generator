# PHASE 9 — Test Audit

## Suite size

100 test files under `accounts/tests/`. Full suite run at the end of
this pass:

```
python manage.py test accounts
...
Ran 838 tests in 176.382s
OK (skipped=9)
```

(Baseline at the start of this pass, before any Phase 9 change: 821
tests, also OK.)

## New tests added this pass (17 total)

| File | Tests | Covers |
|---|---|---|
| `test_phase9_login_throttle.py` | 4 | Login rate limiting (Step 3): correct password still works under the cap, repeated wrong passwords lock out the account (even for a subsequently-correct password), success doesn't burn the failed-attempt budget, per-IP cap catches distributed attempts across different emails |
| `test_phase9_avatar_delete_cleanup.py` | 2 | Avatar file actually removed from storage on account deletion; deletion still works with no avatar ever set |
| `test_phase9_avatar_decompression_bomb.py` | 1 | `DecompressionBombError` now returns a clean 400, exercised against the real Pillow exception (lowered `MAX_IMAGE_PIXELS` for the test, not mocked) |
| `test_phase9_account_deletion_retention.py` | 10 | Financial/audit rows survive deletion and stay correctly attributed + queryable; deleted users can't log in with old credentials; `is_active=False` + unusable password; live subscription cancelled on deletion; every session (not just current) revoked; deleting twice is a safe no-op; deleting one user doesn't touch another's data; original email freed for reuse |

## Pre-existing tests modified (2)

| File | Change | Why |
|---|---|---|
| `test_phase7_profile_delete_session_flush.py` | Added assertions for `is_active=False` and row survival | Semantics changed from hard-delete to anonymize (Step 7) |
| `test_phase9_avatar_delete_cleanup.py` docstring | Corrected to describe anonymize, not hard-delete | Same |

## What was NOT added

Per the brief's own Step 8 rule ("do not pad test counts... add tests
only for real behavior/gaps"), no tests were added for areas that were
audited and found already correct (credit concurrency, generator
duplicate-request handling, webhook idempotency, frontend state machine)
— those already had test coverage from prior phases, re-verified by
running the existing suite, not re-tested from scratch.

## Explicitly not verified in this pass

- **PostgreSQL concurrency** — no PostgreSQL instance was available in
  this environment. The existing concurrency test files ran against
  SQLite only. This is recorded here rather than silently assumed
  equivalent, per the brief's explicit "do NOT label SQLite skips as
  PostgreSQL verification" instruction.
- **Live Razorpay TEST-mode E2E** — no Razorpay TEST credentials were
  available in this environment (`RAZORPAY_KEY_ID`/`SECRET`/
  `WEBHOOK_SECRET` unset). Webhook/payment logic was verified by reading
  the code and by the existing mocked/unit test suite, not by a live
  round-trip against Razorpay's sandbox.
