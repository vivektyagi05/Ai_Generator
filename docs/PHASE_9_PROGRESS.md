# PHASE 9 — Production Readiness + Full System Hardening
## Progress Checkpoint

Status: COMPLETE (see PHASE_9_COMPLETION_AUDIT.md for the full acceptance-gate record; one explicit exception: PostgreSQL concurrency verification not performed, no PostgreSQL available in this environment).

Canonical Phase 9 documents: this file, PHASE_9_SYSTEM_ARCHITECTURE.md, PHASE_9_SECURITY_AUDIT.md, PHASE_9_PRODUCTION_READINESS_AUDIT.md, PHASE_9_TEST_AUDIT.md, PHASE_9_COMPLETION_AUDIT.md.
Phase 9 — update it in place, do not create competing checkpoint files.

## STEP 0 — Forensic Baseline (DONE)

Verified against actual executable state, not prior documentation:

- `python manage.py test accounts` → **821 tests, OK (9 skipped)**.
- `python manage.py check` → 0 issues.
- `python manage.py check --deploy` (pre-fix) → 2 warnings: `security.W004`
  (no `SECURE_HSTS_SECONDS`), `security.W008` (no `SECURE_SSL_REDIRECT`).
- `python manage.py makemigrations --check --dry-run` → no drift.
- Cookies already `HttpOnly` / `SameSite=Lax` / `Secure`-when-`DEBUG=False`.
- `SECRET_KEY` fails hard at import if missing. Razorpay creds are optional
  at import and fail safely (503, nothing logged) at call time if absent —
  correct per the brief's "server authoritative / no secret leakage" rule.
- Conclusion: Phases 1–8B left the app in genuinely solid shape. Phase 9
  work here is targeted hardening, not a rewrite.

## STEP 1 — Production Security Baseline (DONE for this pass)

Findings and fixes:

1. **`SECURE_SSL_REDIRECT` / HSTS not set** — root cause: whether these are
   *correct* depends on deployment topology (is TLS terminated by a proxy
   that itself already redirects HTTP→HTTPS? does that proxy set
   `X-Forwarded-Proto`? can clients bypass the proxy?). Enabling
   `SECURE_SSL_REDIRECT` blindly behind a proxy that already redirects can
   loop; trusting `X-Forwarded-Proto` without confirming the proxy owns
   that header lets a client spoof it. Fixed by making this **opt-in via
   `BEHIND_TLS_PROXY=True`** in `.env`, which sets `SECURE_PROXY_SSL_HEADER`,
   `SECURE_SSL_REDIRECT`, and HSTS (1yr, subdomains, preload) together, with
   the reasoning documented inline in `settings.py` so a future reader
   doesn't just flip it on without the proxy guarantee holding.
   → `manage.py check --deploy` is clean with `BEHIND_TLS_PROXY=True` set;
   unchanged (still 2 warnings) if unset, so nothing regresses for anyone
   not ready to flip it.
2. **`.env.example` was referenced by `settings.py`, `EMAIL_SETUP.md`, and
   3 other docs but did not exist in the repo.** Created it, enumerating
   every env var the app actually reads (verified by grep across the
   codebase, not copied from docs) — `SECRET_KEY`, `DEBUG`, `REDIS_URL`,
   `BEHIND_TLS_PROXY`, the `EMAIL_*`/`BREVO_API_KEY` set, `GROQ_API_KEY`,
   the `RAZORPAY_*` set.
3. `ALLOWED_HOSTS`, `SECRET_KEY` handling, password validators, CSRF/XSS
   middleware ordering — audited, already correct, no change needed.

## STEP 2 — Rate Limiting + Abuse Resistance (DONE for this pass)

`accounts/rate_limit.py` already only talks to Django's cache API
(`cache.get/set/incr`) — its own docstring correctly identified that
**LocMemCache is per-process**, so with multiple Gunicorn workers the
effective OTP/login/AI-generation rate limit becomes
`(configured limit) × (worker count)`, and a client can partially evade one
worker's cooldown by landing on another.

Fix: `CACHES` now reads `REDIS_URL` from the environment. If set, uses
Django's **built-in** `django.core.cache.backends.redis.RedisCache` (no
`django-redis` dependency needed — shipped in Django since 4.0; this
project is on Django 5.2) so all workers/instances share one set of
counters. If unset, falls back to `LocMemCache` (dev/CI keep working with
zero setup) and logs a one-time warning when `DEBUG=False` so the gap is
visible instead of silent. `rate_limit.py` itself needed **zero code
changes** — confirms the module's own design note that it would.

Added `redis==5.0.1` to `requirements.txt` (only imported when `REDIS_URL`
is actually set — the redis client library is required for Django's Redis
backend to function).

Verified: full 821-test suite still passes after this change (rate-limit
tests included); `check --deploy` output unaffected by this change (cache
backend isn't part of that check).

## STEP 3 — Auth + Session Hardening (DONE for this pass)

Audited signup, OTP issue/verify (`accounts/otp_service.py`), login,
forgot-password, logout, account deletion. Most of this was already very
well hardened (session-fixation-safe login via Django's `login()` key
rotation read-before-write around guest-credit migration, enumeration-
resistant forgot-password responses, constant-time OTP comparison,
OTP attempt/expiry limits, GET-proof POST-only logout). Two real gaps
found and fixed:

1. **`user_login` had zero throttling** — unlimited password guesses
   against any account, bounded by nothing. Grepped `accounts/tests/` and
   `accounts/rate_limit.py` first to confirm this wasn't a deliberate,
   tested design choice — it wasn't; no test or rate-limit call ever
   referenced login brute-force resistance. Fixed with two independent
   caps, checked *before* `authenticate()` (so a locked-out attempt
   doesn't pay the password-hashing cost):
   - per-IP, reusing the existing `check_ip_throttle(request, "login")`
     (zero new code — the abstraction was already generic).
   - per-email failed-attempt cap (new `check_login_throttle` /
     `record_failed_login` in `accounts/rate_limit.py`), which
     deliberately records a failure for *every* attempted email —
     including ones with no matching account — so the lockout itself
     can't become a second account-enumeration channel next to the
     already-generic error message.
   - A successful login never counts against the failed-attempt budget.
2. **Account deletion leaked the avatar file** — `UserProfile.avatar` is
   a real file in storage; `user.delete()` CASCADEs the DB row but Django
   never deletes the underlying file. Every account deletion where an
   avatar had ever been uploaded orphaned a file under `media/avatars/`
   permanently (no DB row would ever point at it again to clean it up
   later). Fixed in `profile_delete()` — explicit
   `profile.avatar.delete(save=False)` before `user.delete()`, same
   pattern the re-upload path in `profile_avatar()` already used.

Added `accounts/tests/test_phase9_login_throttle.py` (4 tests: correct
password still works under the cap, repeated wrong passwords lock the
account out even for a subsequent *correct* password, success doesn't
burn the failed-attempt budget, IP cap catches distributed attempts
across different emails) and
`accounts/tests/test_phase9_avatar_delete_cleanup.py` (2 tests: file
actually removed from storage on deletion, deletion still works with no
avatar ever set). Full suite: **827/827 passing** (821 prior + 6 new).

## STEP 4 — Credit/Entitlement Business Integrity (AUDITED — no changes needed)

`accounts/services/credit_service.py` is already financial-grade:
`select_for_update()` row locking inside `transaction.atomic()` on every
mutation (grant/reserve/finalize/refund), a **DB-level**
`UniqueConstraint` on `(credit_account, reference_id, transaction_type)`
that makes double-reserve/double-charge/double-refund impossible even
under a genuine race (not just an application-level check — verified by
reading the actual migration/model, not just the docstring claiming it),
`CheckConstraint`s preventing any negative balance/reserved amount at the
DB level, and `finalize()` never charges more than was reserved even if
the real usage estimate comes back higher. Looked specifically for a real
gap here (this is the highest-stakes area — real money-like state) and
didn't find one worth manufacturing a fix for. Verified, not modified.

## STEP 5 — AI Generator Reliability (AUDITED — no changes needed)

Confirmed all 6 features (`chat, code, story, application, proposal,
photo` — the brief's "5 generators" plus the base chat path) route
through the exact same shared lifecycle in `AI_GENERATORS/api_views.py` +
`accounts/services/ai_usage_service.py` — no second/duplicate generator
controller exists. Verified concretely, including reading the actual
race-condition handling code (not just trusting its comments):
- Duplicate `request_id` racing past the idempotency `SELECT` is caught
  by the same DB `UniqueConstraint` from Step 4 and handed off to the
  same idempotent-replay path (`_replay_existing`), not a raw 500.
- A duplicate of a request that's still mid-flight (status
  `RESERVED`/`REQUESTED`) returns 409 "already being processed"
  **without starting a second provider call** — confirmed this is
  actually wired, not just claimed.
- Both providers (Groq, image) have explicit timeouts + bounded retry;
  any provider failure after reservation refunds the full reservation
  (`complete_failure` → `credit_service.refund`); success charges
  `min(actual, reserved)`, never more than reserved, never fabricated
  token counts when the provider doesn't return usage.
Verified, not modified.

## STEP 6 — Razorpay Webhook/Payment Hardening (AUDITED — no changes needed)

`accounts/webhook_views.py`: signature verified (HMAC-SHA256 over the raw
body) before anything else touches the payload; no signature or an
invalid one is rejected outright, never treated as "maybe legitimate";
CSRF exemption is correct and explicitly reasoned (no session cookie on
this endpoint, signature IS the trust boundary); missing
`X-Razorpay-Event-Id` (Razorpay's documented dedup key) is handled
defensively — event is still processed (never silently dropped) but
logged loudly since idempotency is degraded for that one delivery; a
webhook is always ack'd 200 once durably, idempotently recorded so
Razorpay's retry-until-2xx behavior can't cause reprocessing. Verified,
not modified.

## STEP 7 — Migration Hardening (DONE)

`showmigrations` clean, no drift (Step 0). The one real finding from this
step — self-service account deletion CASCADE-deleting financial/audit
history — was resolved per an explicit business rule from the project
owner: **financial and audit records must not be physically deleted by
self-service account deletion.**

Implementation — `accounts/services/account_deletion_service.py` (new,
follows the existing `accounts/services/*_service.py` convention, not a
parallel architecture):

- **No FK/on_delete changes anywhere.** The design deliberately does NOT
  hard-delete the `User` row at all, so `CreditTransaction`, `AIUsage`,
  `Subscription`, `SubscriptionAuditLog`, and `Payment` — every table this
  matters for — keep their existing `CASCADE` untouched and their rows
  simply never get a delete triggered against them. No nullable-FK
  migration, no risk to the many existing queries/admin views that assume
  `payment.user`/`subscription.user` are never null.
- Instead: **anonymize + deactivate.** `username`/`email` (this project's
  username IS the email) → `deleted-user-<id>@deleted.invalid`,
  `first_name`/`last_name` cleared, `is_active=False`,
  `set_unusable_password()`. Original email is freed for a fresh signup.
- Any live subscription (`PENDING`/`ACTIVE`/`PAST_DUE`/`GRACE_PERIOD`) is
  cancelled through the **existing** `subscription_service.cancel_subscription()`
  — reused as-is, not duplicated; it already revokes the linked
  entitlement and is itself idempotent.
- Every active `Session` row for the user (not just the current one) is
  explicitly swept and deleted — belt-and-suspenders on top of the
  automatic invalidation Django's own session-auth-hash check already
  gives every session the instant the password changes.
- Avatar file still actually deleted from storage (personal data, not a
  financial record); `phone`/`bio` cleared; `UserProfile.deleted_at`
  (new nullable field, migration `0015_userprofile_deleted_at`) marks the
  anonymization and makes the whole operation idempotency-detectable.
- Whole thing wrapped in `transaction.atomic()` with
  `select_for_update()` on the `UserProfile` row, so a double-submit
  race is serialized (second call sees `deleted_at` already set and is a
  clean no-op), same locking pattern `credit_service` already uses.
- `profile_delete()` in `views.py` now just verifies the password and
  calls this one service function — no business logic left in the view.

New `accounts/tests/test_phase9_account_deletion_retention.py` (10
tests) proves every explicitly-required property: financial/audit rows
survive and remain correctly attributed + queryable by original user_id;
deleted users can't log in with old credentials; `is_active=False` +
unusable password; live subscription gets cancelled; **every** session
(tested with two separate logged-in clients) gets revoked; deleting twice
is a safe no-op that doesn't re-anonymize; deleting one account doesn't
touch another user's data at all. Two pre-existing tests
(`test_phase7_profile_delete_session_flush.py`,
`test_phase9_avatar_delete_cleanup.py`) updated for the new semantics
(row survives under a different username, rather than being gone).

Full suite: **838/838 passing** (828 prior + 10 new).

## STEP 8 — File Upload / Media Security

Avatar upload (`profile_avatar`) was already unusually well hardened:
server-side size cap, `Content-Type` header used only as a cheap
pre-filter (explicitly documented as spoofable, not trusted), and real
content-level validation — Pillow actually decodes the file and checks
the real detected format, not just the extension/header.

One real gap found and fixed: `PIL.Image.DecompressionBombError` is a
plain `Exception` subclass (not `OSError`/`ValueError`), so it was **not**
actually caught by the existing `except (UnidentifiedImageError, OSError,
ValueError)` — an image whose declared pixel dimensions exceed Pillow's
own `MAX_IMAGE_PIXELS` guard would raise straight through the view and
500 instead of the clean 400 every other invalid-image case already got.
(Pillow's guard itself already prevents the actual memory-exhaustion risk
— this fixes the response shape/error handling, not a live DoS hole.)
Added `Image.DecompressionBombError` to the caught tuple.

Added `accounts/tests/test_phase9_avatar_decompression_bomb.py` — lowers
`Image.MAX_IMAGE_PIXELS` for the test so a small, real 100×100 PNG
reliably triggers the actual Pillow exception path (not a mock), confirms
400 not 500. Full suite: **828/828 passing** (827 prior + 1 new).

## STEP 9 — Frontend/Backend Contract Audit (AUDITED — no changes needed)

Enumerated every route in `accounts/urls.py` + `AI_GENERATORS/urls.py`
against every `fetch(...)` call and `{% url %}` form action across all 13
templates and 6 static JS files. Every backend capability has a real
caller; no orphan endpoint found.

Checked the other direction too (dummy/dead buttons — explicitly called
out in the brief as never acceptable): searched for the usual tells
(`href="#"`, `onclick="return false"`, `TODO`/`FIXME`, "coming soon") —
none found. Then listed every `<button>` without an inline `onclick=` or
`type="submit"` (the ones most likely to be dead) and traced each `id`/
class selector to its handler. A handful (`deleteAccountBtn`,
`editPersonalBtn`, `resetCodeBtn`, `copyCodeBtn`, etc.) don't appear in
any `static/js/*.js` file, which looked suspicious at first — but they're
wired via inline `<script>` blocks in the same template rather than a
separate JS file, confirmed by grepping each id inside
`profile.html`/`main.html`/`history.html` directly. No dead buttons
found. Verified, not modified.

## Not yet started (next checkpoints — all remaining are P1/P2)

- Step 10 — Frontend reliability (state matrix)
- Step 11 — Frontend design consistency
- Step 12 — Accessibility
- Step 13 — Observability/logging
- Step 14 — Performance
- Step 15 — Failure/recovery matrix
- Step 16 — Testing gap-fill
- Step 17 — Cleanup
- Step 18 — Remaining Phase 9 docs
- Step 19 — Final acceptance gate + verified extracted-zip re-run

## Files touched so far

- `AI_GENERATORS/settings.py` (CACHES, SSL/HSTS block)
- `requirements.txt` (+redis)
- `.env.example` (new)
- `accounts/rate_limit.py` (+login throttle primitives)
- `accounts/views.py` (login throttling wired in; avatar cleanup on
  account deletion)
- `accounts/models.py` (+`UserProfile.deleted_at`)
- `accounts/migrations/0015_userprofile_deleted_at.py` (new)
- `accounts/services/account_deletion_service.py` (new)
- `accounts/views.py` (`profile_delete` now delegates to the service;
  login throttling wired in; avatar decompression-bomb fix)
- `accounts/tests/test_phase9_account_deletion_retention.py` (new)
- `accounts/tests/test_phase7_profile_delete_session_flush.py` (updated
  for anonymize-not-delete semantics)
- `accounts/tests/test_phase9_avatar_delete_cleanup.py` (docstring
  updated for accuracy)
- `accounts/tests/test_phase9_login_throttle.py` (new)
- `accounts/tests/test_phase9_avatar_decompression_bomb.py` (new)
- `PHASE_9_PROGRESS.md` (this file)
