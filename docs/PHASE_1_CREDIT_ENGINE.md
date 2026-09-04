# PHASE 1 — Real AI Credit Engine

Server-authoritative credit accounting for AI_GENERATORS, built on top of the
existing Phase 0 baseline (Django auth, signup/OTP, forgot-password/OTP,
Brevo email, profile, the `/api/ai/` generator endpoint).

> **Read this before enabling Razorpay/subscriptions/premium.** This phase
> deliberately does none of that — see "What this phase does NOT do" below.

> **PHASE 1A UPDATE**: everything in §0 below describes the state Phase 1
> left `/api/ai/` in (`@csrf_exempt`, no rate limiting, minimal
> validation). Phase 1A (this revision) closed every one of those gaps —
> see §12 "AI access, CSRF, and rate-limit policy (Phase 1A)" for the
> current, as-built state, and §14A for what was/wasn't runtime-verified
> for that work. §0 is left as-written below as the historical record of
> what Phase 1 actually found, not edited to look like it was never true.

---

## 0. Forensic review findings (Step 1, Phase 1 — historical)

Before writing any code, the actual baseline was inspected (not assumed).
Two things in the task brief did not match what's in the codebase, and it's
important to say so plainly rather than silently build on top of a
description that wasn't accurate:

- **There is no existing "AI API security foundation."** `/api/ai/`
  (`AI_GENERATORS/api_views.py`) is `@csrf_exempt`, has no
  `@login_required`, no rate limiting, and no request validation beyond
  what this phase adds (non-empty prompt). `accounts/rate_limit.py` exists
  but is only wired into the OTP/auth views, never the AI endpoint.
  Guests and authenticated users alike could call it directly.
  **(Phase 1A: this is fixed — see §12.)**
- **There is no existing credit/usage field anywhere.** Confirmed by
  reading `accounts/models.py` in full — `ChatHistory`, `EmailOTP`,
  `PendingSignup`, `UserProfile`. No duplicate accounting logic exists to
  worry about colliding with.
- **Only one AI feature exists**: text/code generation via Groq's
  OpenAI-compatible chat-completions endpoint (`llama-3.1-8b-instant`),
  called from 4 sites in `templates/main.html` (chat, code-gen, and two
  others) that all hit the same `/api/ai/` endpoint with different prompt
  templates. There is no image generation anywhere in the project.
- Groq's response is non-streaming and OpenAI-compatible, so it includes a
  real `usage` object (`prompt_tokens`/`completion_tokens`/`total_tokens`)
  — this phase uses that for real billing, and only falls back to
  "unavailable" if a response is genuinely missing it (never fabricated).
- The original provider-call retry loop had a latent bug:
  `except (ConnectionError, Timeout):` referenced the *builtin*
  `ConnectionError` and an undefined name `Timeout` — not
  `requests.exceptions.*` — so it would not actually have caught a real
  `requests` timeout/connection error. Fixed as part of this phase's
  rewrite of `api_views.py` (now catches `requests.exceptions.Timeout` /
  `ConnectionError` / `RequestException` explicitly).

Phase 1 (as originally shipped) deliberately did **not** attempt to
retroactively build the missing AI security foundation
(auth/rate-limiting/validation on `/api/ai/`) — that was flagged as a
separate, pre-existing gap. **Phase 1A (this revision) is exactly that
follow-up work** — see §12.

---

## 1. Architecture

```
accounts/
  models.py                      # CreditAccount, CreditTransaction, AIUsage (appended)
  credit_config.py                # credit *pricing* numbers, one place, settings-overridable
  ai_request_config.py            # PHASE 1A: request *validation* limits (Step 4) — the single
                                   #   source api_views.py enforces against AND main.html reads
                                   #   from (via json_script), so the two never duplicate numbers
  rate_limit.py                   # OTP/IP throttling (pre-existing) + PHASE 1A: check_ai_rate_limit()
  services/
    credit_service.py             # the ONLY code allowed to mutate CreditAccount balances
    ai_usage_service.py           # ties AIUsage rows to credit_service for one AI request
    guest_identity.py             # server-issued guest identity via Django sessions
    exceptions.py
  migrations/
    0006_credit_engine.py         # hand-written (see file header — Django unavailable here)
  management/commands/
    grant_monthly_credits.py      # infrastructure only, not scheduled (Step 10)
  tests/
    test_credit_models.py
    test_credit_service.py
    test_ai_credit_integration.py
    test_credit_security.py
    test_ai_csrf.py               # PHASE 1A
    test_ai_rate_limit.py         # PHASE 1A
    test_ai_request_validation.py # PHASE 1A
    test_ai_idempotency_conflict.py  # PHASE 1A
    test_credit_concurrency.py    # PHASE 1A (Postgres-gated — see §6)

AI_GENERATORS/
  api_views.py                    # /api/ai/ — Phase 1 credit lifecycle + PHASE 1A hardening
  views.py                        # PHASE 1A: create() now passes ai_request_config into the template
```

**Core rule enforced throughout:** nothing outside `credit_service.py`
touches `CreditAccount.balance` or `.reserved_balance` directly. Every
mutation goes through `grant()` / `reserve()` / `finalize()` / `refund()`,
each of which: runs in `transaction.atomic()`, takes
`select_for_update()` on the account row, and writes exactly one
`CreditTransaction` ledger row describing the change.

---

## 2. Models

### CreditAccount
One row per identity — either a `User` (`OneToOneField`) **or** a
server-issued guest session key (`guest_session_key`, unique), enforced by
a `CheckConstraint` (`ck_creditaccount_exactly_one_owner`) so an account
can never have both or neither. Fields: `balance`, `reserved_balance`,
`lifetime_earned`, `lifetime_used`, `last_monthly_grant_period`. DB-level
`CheckConstraint`s guarantee `balance >= 0`, `reserved_balance >= 0`, and
`balance >= reserved_balance` (i.e. `available_balance` can never go
negative) — these hold even if application code has a bug, on any backend
that supports table CHECK constraints (SQLite and Postgres both do).

All monetary fields use `PositiveBigIntegerField` (integers) — no floats
anywhere in the accounting path.

### CreditTransaction
Append-only ledger. `transaction_type` is one of `GRANT`, `BONUS`,
`RESERVE`, `USAGE`, `REFUND`, `EXPIRATION`, `ADMIN_ADJUSTMENT` (a
`TextChoices`, not a free string — Step 2's "stronger integrity" request).
`RESERVE` is an addition beyond the task's minimum list, because a
reservation *is* a mutation of account state (`reserved_balance`) and Step
12 requires every mutation to be audited, not just ones that touch
`balance`.

Every row stores `balance_before/after` and `reserved_before/after`, plus
`reference_id` (the causing `AIUsage.request_id`, or an admin/grant
reference). A composite constraint,
`uq_credittxn_account_reference_type` (`UNIQUE (credit_account,
reference_id, transaction_type) WHERE reference_id != ''`), is the
database-level backstop for idempotency: the same request can never
reserve, finalize, or refund twice, even if application logic raced.
Ledger rows are never edited or deleted by application code (the admin
registration also disables change/delete permissions for this model).

### AIUsage
One row per AI request that was attempted, keyed by a unique `request_id`
(the idempotency key for the whole lifecycle). Tracks
`input_tokens`/`output_tokens`/`total_tokens` (nullable) plus an explicit
`usage_unavailable` boolean for the case where the provider didn't return
usage — Step 8 requires never fabricating token numbers, so "we don't
know" is a real, queryable state rather than a fake `0`.
`credits_reserved`/`credits_charged`/`credits_refunded` and a `status`
`TextChoices` covering the full lifecycle (`REQUESTED` →
`VALIDATION_FAILURE` | `INSUFFICIENT_CREDITS` | `RESERVED` → `FINALIZED` |
`REFUNDED`). A small `metadata` JSON field carries a bounded
`result_preview` so an idempotent replay of a `FINALIZED` request can
return the same response without re-billing or re-calling the provider —
this is a deliberate, documented tradeoff (see Known Limitations).

---

## 3. Credit service (Step 3)

`accounts/services/credit_service.py` exposes:

- `get_account(user=None, guest_key=None)` — read-only lookup, `None` if none exists.
- `get_or_create_account(user=None, guest_key=None)` — creates on first use, applying `INITIAL_FREE_CREDITS` exactly once (idempotent under a race — `get_or_create` handles the create-race; the initial grant's `reference_id` is `initial-grant:<account.pk>`, deduplicated by the ledger constraint).
- `get_balance_snapshot(account)` — fresh `balance`/`reserved_balance`/`available_balance`/`lifetime_earned`/`lifetime_used`.
- `can_afford(account, amount)` — best-effort, non-locking hint only. The authoritative check is inside `reserve()`.
- `grant(account, amount, transaction_type, reference_id, ...)` — `GRANT`/`BONUS`/`ADMIN_ADJUSTMENT`/`EXPIRATION` only. Never touches `reserved_balance`.
- `reserve(account, amount, reference_id, ...)` — atomically holds credits against `available_balance`; raises `InsufficientCreditsError` if it can't.
- `finalize(account, reservation_amount, actual_amount, reference_id, ...)` — converts a reservation into a real charge; **always releases the full reservation**, but **never charges more than was reserved** even if `actual_amount` comes back higher (see Known Limitations).
- `refund(account, reservation_amount, reference_id, ...)` — releases a reservation with zero charge.
- `grant_monthly_credits(account)` — idempotent per `(account, "YYYY-MM")`; infrastructure only (Step 10), not on a schedule (see §7).

## 4. AI usage orchestration

`accounts/services/ai_usage_service.py` is the AI-specific glue between
`AIUsage` and `credit_service`, so `AI_GENERATORS/api_views.py` stays a
thin HTTP adapter:

```
check_existing_usage(request_id, user, guest_key)   # idempotency lookup, identity-scoped
        |
begin_request(...)   # validate -> estimate cost -> reserve  (raises ValidationFailure / InsufficientCreditsError)
        |
[ provider call stays in api_views.py ]
        |
complete_success(...)   OR   complete_failure(...)
   finalize the charge          refund the full reservation
```

## 5. Reservation lifecycle end-to-end (Step 5 / Step 9)

```
POST /api/ai/
  -> parse JSON (400 on malformed JSON, no AIUsage row -- never reached identity/provider)
  -> resolve identity: authenticated User, or guest via Django session (guest_identity.py)
  -> idempotency check on request_id (identity-scoped)
       - FINALIZED    -> replay cached result, no new charge
       - VALIDATION_FAILURE / INSUFFICIENT_CREDITS / REFUNDED -> replay the same outcome
       - RESERVED/REQUESTED -> 409, "already being processed"
  -> begin_request(): validate prompt non-empty -> estimate cost -> reserve()
       - empty prompt        -> AIUsage(VALIDATION_FAILURE), 400, NO reservation made
       - insufficient credits -> AIUsage(INSUFFICIENT_CREDITS), 402, NO reservation left dangling
  -> call Groq (existing retry-twice/15s-timeout behavior, now with correctly-typed exception handling)
  -> success            -> complete_success(): finalize() charges real token-derived cost, AIUsage=FINALIZED, 200
  -> timeout             -> complete_failure(): refund(), AIUsage=REFUNDED, 503
  -> network failure      -> complete_failure(): refund(), AIUsage=REFUNDED, 503
  -> HTTP 4xx/5xx from Groq -> complete_failure(): refund(), AIUsage=REFUNDED, 502
  -> malformed response  -> complete_failure(): refund(), AIUsage=REFUNDED, 502
```

Response contract for the existing frontend is preserved: `{"result":
"..."}` on 200, `{"error": "..."}` otherwise. Everything else added
(`request_id`, `credits_charged`, `credits_available`, `replayed`) is
additive JSON the existing `templates/main.html` call sites don't read, so
they keep working unmodified.

## 6. Concurrency (Step 4 / PHASE 1A Step 7)

`reserve()`/`finalize()`/`refund()`/`grant()` all take
`select_for_update()` on the `CreditAccount` row inside
`transaction.atomic()`. On **PostgreSQL** (this project already depends on
`psycopg2-binary`, presumably the production database) this gives real
row-level locking: two concurrent `reserve()` calls for the same account
serialize, and the second sees the first's committed `reserved_balance`
before deciding whether it can afford its own reservation.

On **SQLite** (this project's dev/default `DATABASES` setting),
`select_for_update()` is a documented Django no-op — it silently omits
`FOR UPDATE` rather than erroring, and the actual serialization comes from
SQLite's own file-level write locking within the transaction. That's
weaker isolation than Postgres gives, and is called out explicitly as a
known limitation rather than glossed over. In all cases the **outcome**
that Step 4 actually requires — a negative `available_balance` is
impossible — is enforced twice more, independent of locking: the Python
check inside `reserve()`, and the DB `CheckConstraint`
`ck_creditaccount_balance_gte_reserved` as a hard backstop.

**PHASE 1A**: `accounts/tests/test_credit_concurrency.py` adds a real
two-thread concurrency test (`TransactionTestCase` + `threading.Barrier`,
two threads both trying to reserve 8 credits against a 10-credit balance —
only one may succeed). It is explicitly gated with
`@unittest.skipUnless(connection.vendor == "postgresql", ...)` and skips
itself under this project's default SQLite config, **on purpose** — a
"pass" on SQLite would be a false positive given the no-op locking above,
not evidence the protection works. This test has **not** been executed in
this environment (no Django available at all — see §14A) and, even once
Django is available, will only meaningfully run against a Postgres
`DATABASES` config. See PHASE_1_COMPLETION_AUDIT.md for exact commands.

## 7. Idempotency (Step 6 / PHASE 1A Step 5)

`AIUsage.request_id` is the idempotency key. **PHASE 1A**:
`templates/main.html`'s `aiRequest()` helper now always sends one —
generated once per logical "Generate" click (`newAiRequestId()`), reused
if that same click's attempt fails and the user retries (the generator's
`*GenState.requestId` is only cleared back to `null` on success), and
replaced with a fresh id the next time a *new* generation starts. A
caller that omits `request_id` entirely still works — the server
generates one — but that only protects a single call from being
double-processed, not a naive retry (see `_clean_request_id()` in
`api_views.py`).

Three layers enforce it, end to end:
1. **Application layer**: `check_existing_usage()` looks up `request_id`
   and, if found *and owned by the same caller identity*, replays the
   stored outcome instead of re-running anything. The replay branches on
   status (`_replay_existing()`):
   - `FINALIZED` → replays the stored result + charge (no re-billing, no
     second provider call).
   - `VALIDATION_FAILURE` / `INSUFFICIENT_CREDITS` → replays the same
     error.
   - `REFUNDED` → tells the caller the prior attempt failed and was
     refunded, safe to submit as a *new* request.
   - `RESERVED` / `REQUESTED` (still in flight) → **PHASE 1A**: returns
     `409 Conflict` rather than silently proceeding — see
     `accounts/tests/test_ai_idempotency_conflict.py`.
2. **Database layer**: `uq_credittxn_account_reference_type` makes it
   physically impossible to write two `RESERVE`, two `USAGE`, or two
   `REFUND` rows for the same `(account, request_id)` — even under a race
   the app-layer check missed, the second attempt gets an `IntegrityError`
   → `DuplicateRequestError`.
3. **Request-shape validation** (PHASE 1A): a caller-supplied
   `request_id` must match `^[A-Za-z0-9_\-]{1,64}$` (`ai_request_config.
   REQUEST_ID_RE`) or the request is rejected with 400 before it can reach
   `AIUsage.request_id` (`max_length=64`) — this is what stops a hostile
   id from ever producing an uncaught `DataError`/`IntegrityError` instead
   of a clean validation response.

## 8. Credit calculation (Step 7 / Step 8)

See `accounts/credit_config.py`'s module docstring for the full
reasoning; summary:

- Only feature: text generation via Groq (`llama-3.1-8b-instant`). No
  image generation exists, so no image-credit formula was invented for it
  (Step 7 explicitly forbids pretending token accounting exists for a
  provider/feature that doesn't expose it).
- **Reservation** (pre-call, before real usage is known): a conservative
  character-based token estimate (`len(prompt) / 4`, rounded up) plus the
  fixed `max_tokens=1024` the payload already requests, converted to
  credits and rounded up. Always reserves at least as much as the real
  charge should be.
- **Actual charge** (post-call): Groq's real `usage.total_tokens` when
  present, converted via the same ratio. If `usage` is missing from the
  response, charge exactly the reservation amount and mark
  `usage_unavailable=True` — never fabricate a token count.
- **Conversion ratio**: `CREDIT_PER_1000_TOKENS = 1` (1 credit per 1000
  tokens, rounded up, minimum 1 credit per successful request). This is an
  **internal accounting unit**, explicitly documented as a placeholder —
  Phase 0 never established real business numbers for this, and this file
  says so rather than inventing an authoritative-sounding one.
- All of the above are Django-settings-overridable
  (`CREDIT_PER_1000_TOKENS`, `CREDIT_MINIMUM_CHARGE`,
  `CREDIT_ESTIMATED_CHARS_PER_TOKEN`, `CREDIT_DEFAULT_MAX_COMPLETION_TOKENS`,
  `CREDIT_INITIAL_FREE_GRANT`, `CREDIT_MONTHLY_FREE_GRANT`), so a real
  pricing decision later is a config change, not a rewrite.
- **PHASE 1A note on "actual exceeds reservation"**: `finalize()` never
  charges more than was reserved (`credit_service.finalize()` clamps to
  `min(actual_amount, reservation_amount)` and tags the transaction
  metadata `actual_exceeded_reservation: true` if it ever happens). Given
  the reservation formula reserves for the full fixed `max_tokens=1024`
  ceiling every time, this should be structurally impossible for the
  current single feature/provider — documented here as a safety net, not
  as something observed.

## 9. Guest behavior (Step 9 / PHASE 1A Step 9)

No permanent fake `User` row is ever created for a guest. Guest identity
is the Django session framework's server-issued, signed session key
(`accounts/services/guest_identity.py`) — not anything the client sets,
sends as a header, or stores in `localStorage`. `CreditAccount` supports
this natively via the nullable `guest_session_key` column (see §2).

**Known, documented limit of this approach** (not hidden): a guest who
clears cookies or opens a private window gets a brand-new empty session,
and therefore a brand-new `CreditAccount` with a fresh initial grant. This
is a materially narrower gap than "the frontend sends its own balance"
(which this phase closes completely), but it is real.

**PHASE 1A**: this is now bounded, not eliminated, by
`rate_limit.check_ai_rate_limit()`'s per-IP guest cap
(`AI_GUEST_IP_MAX_PER_WINDOW`, default 20/hour) — cookie-clearing gets a
fresh session and a fresh grant, but every session from the same IP still
counts against one shared IP-level budget, so cycling sessions to farm
free grants runs into that ceiling. This is explicitly a bound, not a
fix: a distributed attacker (many IPs) is not addressed here — that would
need device fingerprinting or phone verification, called out as future
work, not attempted (Claude does not fingerprint users without a strong
justification, and none exists yet for this project). See §12 for the
full rate-limit design (session cap + IP cap, independently enforced).

- **Initial grant**: `CREDIT_INITIAL_FREE_GRANT = 50` (placeholder),
  applied once per account the first time it's created (user or guest).
- **Monthly grant**: `CREDIT_MONTHLY_FREE_GRANT = 20` (placeholder),
  applied by `credit_service.grant_monthly_credits(account)`, idempotent
  per `(account, calendar month)`. **Not wired to any scheduler** — this
  project has no Celery/cron worker of its own (same situation as the
  existing `cleanup_expired_otps` management command). Run
  `python manage.py grant_monthly_credits` from whatever external
  scheduler the deployment platform offers (e.g. a Render Cron Job), once
  a month. No pricing UI was built (explicitly out of scope).

## 11. Read-only balance API (Step 14)

`GET /credits/balance/` (`accounts.views.credit_balance`) returns the
caller's own `CreditAccount` snapshot — `balance`, `reserved_balance`,
`available_balance`, `lifetime_earned`, `lifetime_used` — computed purely
from the database via `credit_service.get_balance_snapshot()`. Identity
comes from the session exactly like the AI endpoint (never from the
request body). No profile dashboard UI was built (explicitly out of
scope) — this is the backend API a future dashboard would call.

## 12. Security findings (Step 15, Phase 1 — historical) / AI access, CSRF, and rate-limit policy (PHASE 1A, current)

**Phase 1 (historical) found:**
- **Closed by Phase 1**: balance/deduction/refund/usage/limits are 100%
  server-computed; nothing in `/api/ai/` or `/credits/balance/` reads a
  credit amount from the request body (tested explicitly — see
  `test_credit_security.py::NoClientControlledAmountTests`); cross-account
  balance access isn't possible since identity is session-derived, not a
  request parameter; race conditions are covered per §6; duplicate/replay
  charging is covered per §7; all amount inputs are validated positive/
  non-zero as appropriate (`InvalidAmountError`) before ever reaching the
  database.
- **NOT closed by Phase 1 (flagged then, fixed now — see below)**:
  `/api/ai/` was `@csrf_exempt` with no rate limiting and minimal request
  validation.

**PHASE 1A closes every one of those gaps.** This section documents the
current, as-built policy — read it as the source of truth over §0/§12's
historical framing above.

### AI access policy (Step 1 / Step 11)

`AI_GENERATORS/api_views.py:_check_access_policy(user, guest_key)` is the
single, explicit place this is decided:

| Identity | Access | Bounded by |
|---|---|---|
| Guest (no login) | Allowed — limited demo access | `rate_limit.check_ai_rate_limit()`'s guest-session cap (8/hour) **and** guest-IP cap (20/hour) — see below |
| Authenticated (any logged-in user) | Allowed | `rate_limit.check_ai_rate_limit()`'s per-user cap (40/hour) |
| Premium | **Not implemented.** Explicitly out of scope per the task's STOP CONDITION. `_check_access_policy()` and `check_ai_rate_limit()` are both structured so that when entitlements exist, they're the two places a premium branch gets added — no other part of the request lifecycle needs to change. |

Guest access was deliberately **not** removed/gated behind
`@login_required` — Phase 0 planned a Guest → Free → Premium funnel, and
killing guest AI access would break that funnel, not secure it. What
actually needed fixing was guests (and authenticated users) being
*unbounded* — see rate limiting below.

### CSRF policy (Step 2)

`/api/ai/` no longer carries `@csrf_exempt`. It is now protected exactly
like every other state-changing view in this project, by Django's
`CsrfViewMiddleware` (already in `MIDDLEWARE`, was just bypassed for this
one view before). A request without a valid CSRF token (cookie set +
matching `X-CSRFToken` header) is rejected with the framework's own `403`
**before `gemini_api()` runs at all** — there is no code in this project's
view to audit for that, it's Django's own middleware.

The frontend side: `templates/main.html`'s new `aiRequest()` helper reads
the CSRF token from the `csrftoken` cookie (falling back to the
server-rendered `{{ csrf_token }}` template variable) and sends it as
`X-CSRFToken` on every call — see the helper's docstring in `main.html`.
All 4 existing generator call sites (code/story/application/proposal)
were switched to call `aiRequest()` instead of duplicating `fetch()` +
header logic, so there's one place this can go stale, not four.

**What this does and doesn't protect against**: CSRF protection stops a
different site from silently spending a logged-in user's credits by
tricking their browser into POSTing to `/api/ai/` using their existing
session. It does **not** protect against a user's own scripted/automated
abuse of their own account (that's what rate limiting is for) or a guest
session being reused (that's session/cookie security, unaffected by this
change).

### Rate-limit behavior (Step 3)

`accounts/rate_limit.py:check_ai_rate_limit()` — sliding-window counters
on Django's cache framework (same mechanism the pre-existing OTP
throttling uses, see that module's docstring for the LocMemCache
per-process caveat, which applies here identically). Enforced **after**
identity resolution / access policy and **before** request body parsing —
so a client over its cap never gets its JSON parsed, and a client under
its cap is still fully validated afterward.

| Tier | Cap | Window | Notes |
|---|---|---|---|
| Authenticated | 40 requests | 1 hour | Per user id. Premium shares this until entitlements exist (Step 3 explicitly allows this for now). |
| Guest (per session) | 8 requests | 1 hour | Per server-issued guest session key. |
| Guest (per IP) | 20 requests | 1 hour | Summed across every guest session from that IP — see §9's cookie-clearing discussion. |

A blocked request gets `HTTP 429` with a `Retry-After` header; the JSON
body is a generic "slow down" message — Step 3's "don't leak sensitive
information" is honored by never naming which cap (session vs IP vs user)
was hit in the response, only in server-side logging/metrics.

Every request that reaches the rate-limit check counts against it,
including ones that go on to fail validation, hit insufficient credits, or
error out at the provider — this matches the existing `check_ip_throttle`
semantic elsewhere in this module and denies an attacker a "free" way to
exhaust rate-limit budget by sending requests designed to fail downstream.

### Request validation (Step 4)

`accounts/ai_request_config.py` is the single source of these numbers —
both `api_views.py` (enforcement) and `templates/main.html` (a client-side
UX hint only, via `{{ ai_request_limits|json_script:"ai-request-limits" }}`
rendered by `AI_GENERATORS/views.py:create()`) read from it, so they can't
drift apart:

- **Content-Type**: must be exactly `application/json` or `400`.
- **Body size**: raw body over `AI_MAX_BODY_BYTES` (default 20,000 bytes)
  is `413`, checked before the body is even JSON-decoded.
- **Prompt length**: over `AI_MAX_PROMPT_CHARS` (default 4,000 chars) is
  `400`, checked before any credit reservation.
- **Feature**: must be one of `AI_ALLOWED_FEATURES` (default
  `chat,code,story,application,proposal`) or `400` — omitted entirely
  still defaults to `chat` for backward compatibility with any caller that
  predates this field.
- **request_id shape**: `^[A-Za-z0-9_\-]{1,64}$` or `400` — see §7.
- **Malformed JSON** / **non-object JSON body**: `400` (pre-existing
  behavior, unchanged).

None of the above ever creates an `AIUsage` row or touches a
`CreditAccount` — they're pre-flight rejections, before any reservation is
attempted, so there's nothing to refund.

### Guest abuse (Step 9) — see §9 for the full writeup

Summary: session-based identity (unchanged from Phase 1) + PHASE 1A's
per-IP guest rate-limit cap, which bounds (does not eliminate) the
cookie-clearing hole. No device fingerprinting was added — Claude/this
implementation does not fingerprint users without a strong justification,
and none exists for this project yet.

## 13. Tests (Step 16)

- `test_credit_models.py` — account creation/uniqueness, guest accounts,
  ownership constraint, balance/reserved-balance DB-level non-negativity.
- `test_credit_service.py` — grant / reserve (success, insufficient,
  exact-balance, duplicate reference) / finalize (normal, over-reservation
  clamp, duplicate) / refund (normal, duplicate) / monthly grant
  idempotency / a best-effort real-thread concurrency test.
- `test_ai_credit_integration.py` — full HTTP-level flow through
  `/api/ai/`: success charges credits, missing-usage doesn't fabricate
  tokens, HTTP/timeout/network/malformed provider failures all refund,
  empty-prompt validation never charges, insufficient credits blocks with
  402, duplicate `request_id` doesn't double-charge (and doesn't call the
  provider twice), a spoofed `credits`/`balance` field in the request body
  has no effect, and guest sessions each get an independent account.
- `test_credit_security.py` — cross-user balance isolation, no request
  parameter can target another account, and an explicit sweep of
  plausible "attacker sets a credit field" request bodies.
- **`test_ai_csrf.py`** (PHASE 1A) — missing CSRF token → 403; valid CSRF
  token → 200; stale/mismatched CSRF token → 403. Uses
  `Client(enforce_csrf_checks=True)` deliberately (the default test
  `Client` disables CSRF checking, which would make these tests pass
  regardless of whether the view is actually protected).
- **`test_ai_rate_limit.py`** (PHASE 1A) — authenticated user eventually
  gets 429 (with `Retry-After`, no mechanism details leaked in the body);
  rate limit is per-user, not shared; guest eventually gets 429; guest
  per-IP cap bounds session-cycling across many fresh `Client()` sessions.
- **`test_ai_request_validation.py`** (PHASE 1A) — malformed JSON,
  non-object JSON, oversized body, oversized prompt, invalid feature,
  invalid content type, invalid/overlong request_id all rejected before
  the provider is ever called (`mock_post.assert_not_called()` on every
  one); a valid feature and an omitted feature (→ defaults to `chat`) are
  both accepted.
- **`test_ai_idempotency_conflict.py`** (PHASE 1A) — a `request_id`
  belonging to a still-`RESERVED` (in-flight) `AIUsage` row gets `409`,
  never a second provider call, never a second reservation.
- **`test_credit_concurrency.py`** (PHASE 1A) — two-thread concurrent
  `reserve()` race; `@unittest.skipUnless(connection.vendor ==
  "postgresql", ...)`, so it self-skips on this project's default SQLite
  config rather than reporting a false pass — see §6.

## 14. Tests actually executed (Step 17, Phase 1) — READ THIS

**Django is not installed in this build/review environment, and the
environment's network access is disabled** (`pip install django` and `pip
download django` both fail with "No matching distribution found" — there
is no cached wheel anywhere on disk either). This is a pre-existing
constraint of the sandbox this work was produced in, not a project issue.

Concretely, none of these were run, and this report does **not** claim
they were:

```
python manage.py check
python manage.py makemigrations --check
python manage.py test
python manage.py test accounts
```

What **was** done instead, as partial, honest verification:

```
find . -name "*.py" | xargs python3 -m py_compile     # -> ALL COMPILE OK (every .py file in the project, old and new)
```

This confirms every new/changed file is syntactically valid Python 3 and
free of import-order-independent `NameError`-at-parse-time mistakes — the
kind of bug the original `api_views.py`'s `except (ConnectionError,
Timeout):` had (see §0). It does **not** confirm the Django ORM/migration
graph is valid, that the test suite passes, or that the hand-written
migration (`0006_credit_engine.py`) matches exactly what
`makemigrations` would generate.

**You must run the four commands above yourself** in an environment with
this project's `requirements.txt` installed and a `.env` with `SECRET_KEY`
before treating this phase as verified. If `makemigrations --check`
reports a difference, trust `makemigrations`' output over
`0006_credit_engine.py` and regenerate that file.

## 14A. Tests actually executed (PHASE 1A) — READ THIS

**Same environment constraint as §14** — Django is still not installed and
network access is still disabled in this environment; nothing changed
about that between Phase 1 and Phase 1A. Concretely, in addition to §14's
four commands, none of these were run either:

```
python manage.py test accounts.tests.test_ai_csrf
python manage.py test accounts.tests.test_ai_rate_limit
python manage.py test accounts.tests.test_ai_request_validation
python manage.py test accounts.tests.test_ai_idempotency_conflict
python manage.py test accounts.tests.test_credit_concurrency   # only meaningful against Postgres — see §6
```

What **was** done instead, as partial, honest verification, beyond
re-running §14's `py_compile` sweep (still all-OK against the full,
now-larger file set):

```
node --check <extracted <script> blocks from templates/main.html>   # -> SYNTAX OK
```

Every Django template tag (`{{ csrf_token }}`,
`{{ request.user.is_authenticated|yesno:... }}`) was substituted with a
plausible literal value before running this check, since those aren't
valid JS on their own — this confirms the *JavaScript* Phase 1A added
(the `aiRequest()` helper and the 4 updated call sites) has no syntax
errors, not that it behaves correctly at runtime in a browser (no browser
or DOM was available to test against either).

Also done: a manual code-review trace of every new test file against the
view/service code it exercises (see §12/§13 above for what each test
asserts and why), and a manual grep-based re-verification of §0's
"nothing outside `credit_service.py` touches `balance`/`reserved_balance`/
`lifetime_*` directly" claim, specifically re-run after Phase 1A's changes
(result: still true — Phase 1A added no new mutation site; `grep -rn
"\.balance *=\|\.reserved_balance *=\|\.lifetime_used *=\|\.lifetime_earned
*=" --include="*.py" .` outside `migrations/` and `tests/` only matches
`accounts/services/credit_service.py`).

**You must run the five commands above yourself**, in addition to §14's
four, before treating Phase 1A as verified. See
`PHASE_1_COMPLETION_AUDIT.md` for the full requirement-by-requirement
status table and the consolidated list of commands to run.

## 15. Regression (Step 18)

Not runtime-verified for the same reason as §14/§14A. By inspection,
Phase 1 (original): no existing file was deleted; `accounts/models.py`
had only new classes appended (existing
`ChatHistory`/`EmailOTP`/`PendingSignup`/`UserProfile` untouched);
`accounts/views.py` had only a new `credit_balance` view appended;
`accounts/urls.py` had only one new route inserted; `accounts/admin.py`
had only new registrations added; no existing migration file was modified
(0006 only adds new tables/constraints, no `AlterField` on anything
pre-existing).

**PHASE 1A, additionally by inspection**: `AI_GENERATORS/views.py`'s
`create()` gained one new context key (`ai_request_limits`) passed to an
otherwise-unmodified `render()` call — every existing template variable
main.html already used (`csrf_token`, `request.user.is_authenticated`,
etc.) is untouched. `templates/main.html`'s edits are scoped to: one new
`json_script` tag + one new `<script>` block near the top (the
`aiRequest()` helper and its supporting functions), and the bodies of the
4 existing `generate*()` functions (their DOM element IDs, prompt
construction, and post-success rendering logic were **not** changed — only
the fetch/CSRF/request_id/error-handling portion). No `<form>`, no
button `id`/`onclick` wiring, no other tab's markup was touched. Login,
signup, OTP flows, forgot-password, and profile management code paths
(both backend views and their templates) were not touched by either Phase
1 or Phase 1A. **This needs to actually be run**
(`python manage.py test accounts`, plus a manual click-through of
login/signup/profile **and** all 4 AI generators end-to-end in a browser)
before being called verified.

## 16. Known limitations

- **Not runtime-verified** — see §14/§14A/§15. This is the single biggest
  caveat on this whole phase (both Phase 1 and Phase 1A).
- **Hand-written migration** (`0006_credit_engine.py`) — written to match
  `accounts/models.py` field-for-field, but not diffed against real
  `makemigrations` output. See the file's own header comment. PHASE 1A
  added no new models/fields, so no new migration was needed for this
  revision.
- **`/api/ai/` security gaps from Phase 1 are now closed** (CSRF,
  authentication-policy clarity, rate limiting, request validation — see
  §12) — kept here as a crossed-out line rather than deleted, so the
  history of what was fixed is visible: ~~no CSRF protection, no
  authentication requirement, no rate limiting~~.
- **Guest tracking is session-based; the per-IP rate-limit cap bounds but
  does not eliminate cookie-clearing abuse** — see §9. A distributed
  attacker using many IPs is not addressed; that would need device
  fingerprinting or phone verification, deliberately not implemented
  (no strong justification exists for this project yet).
- **Rate limiting is per-process (LocMemCache by default)** — see
  `accounts/rate_limit.py`'s module docstring. Multiple gunicorn workers
  (per `Procfile`) each keep their own counters, so the effective limit in
  production is roughly (configured limit) × (worker count). Point
  `CACHES` at a shared backend (e.g. Redis) to close this — no code change
  needed in `rate_limit.py` itself.
- **Monthly grant has no scheduler** — `grant_monthly_credits` management
  command exists but nothing calls it automatically. See §10.
- **`finalize()` never charges beyond what was reserved** — if the real
  cost estimate under-reserves (shouldn't happen given the conservative
  formula, but if it did), the business absorbs the gap rather than the
  user being charged for it. This is a deliberate direction for the
  failure to fail in (see `credit_service.finalize()` docstring).
- **Credit-to-token ratio is a placeholder**, not a real pricing decision
  — see §8.
- **Result replay payload**: `AIUsage.metadata.result_preview` stores up
  to 4000 characters of a successful result so a duplicate `request_id`
  can be answered identically without re-billing. This is more provider
  output retained server-side than existed before (previously nothing was
  persisted beyond `ChatHistory`, which is only written for authenticated
  users' explicit "save" action). Worth a privacy/retention look before
  this ships broadly — not blocking for Phase 1A, but noted rather than
  silently introduced.
- **Frontend retry is manual, not automatic**: PHASE 1A's `aiRequest()`
  helper and the 4 generator functions correctly *reuse* a request_id if
  the user clicks "Generate" again after a failure, but there is no
  automatic retry-on-timeout loop — the user has to click again
  themselves. This matches the task's Step 13 requirement ("allow retry")
  without adding auto-retry behavior that wasn't asked for.
- **Concurrency test is Postgres-only and has not been run at all** — see
  §6 and `accounts/tests/test_credit_concurrency.py`'s module docstring.

## 17. What this phase does NOT do

Per the task's explicit scope: no Razorpay, no subscriptions, no Premium
entitlements, no fake payment or Premium data, no pricing UI, no complete
profile dashboard, no changes to login/signup/OTP/forgot-password flows.
This remains true for PHASE 1A as well — nothing in this revision touches
Premium, payments, or entitlements; `_check_access_policy()` explicitly
returns `None` (allow) for every current identity type and documents where
a Premium branch would go, without adding one.
