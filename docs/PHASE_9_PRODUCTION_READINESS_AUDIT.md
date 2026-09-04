# PHASE 9 — Production Readiness Audit

## Deployment configuration

| Setting | State |
|---|---|
| `manage.py check` | 0 issues |
| `manage.py check --deploy` | 0 issues with `BEHIND_TLS_PROXY=True` set (see PHASE_9_SECURITY_AUDIT.md) |
| `manage.py makemigrations --check --dry-run` | No drift |
| Cache backend | `REDIS_URL`-driven; `LocMemCache` fallback for dev only, with a loud warning if missing in a non-DEBUG environment |
| `.env.example` | Created — previously referenced by 4+ files but didn't exist |
| Static files | `whitenoise` already configured |
| Gunicorn | Already in `requirements.txt`; multi-worker-safe now that rate limiting doesn't depend on a per-process cache |

## Step 15 — Failure / Recovery Matrix

Built from actually reading the relevant code path for each row, not
assumed from the architecture description.

| Operation | Failure | DB state | Credit state | Subscription state | UI state | Recovery |
|---|---|---|---|---|---|---|
| AI generation success | — | `AIUsage.FINALIZED` | Charged `min(actual, reserved)` | unaffected | `SUCCESS` state, result rendered | — |
| AI generation timeout | Provider didn't respond in time | `AIUsage.FAILED` (provider_timeout) | Reservation refunded in full | unaffected | `NETWORK_ERROR`/`ERROR` state, retry available | Retry re-enters `VALIDATING` → new `request_id` |
| AI provider failure (non-timeout) | Provider returned an error | `AIUsage.FAILED` | Reservation refunded in full | unaffected | `ERROR` state with message | Retry available |
| AI malformed provider response | Response didn't parse as expected | `AIUsage.FAILED` | Reservation refunded in full | unaffected | `ERROR` state | Retry available |
| AI duplicate request (same `request_id`, first already finished) | — (not a failure) | Existing `AIUsage` row read, not re-created | Not re-charged (idempotent replay) | unaffected | Result replayed to a second identical response | — |
| AI duplicate request (same `request_id`, first still in flight) | — (not a failure) | Existing row still `RESERVED`/`REQUESTED` | Not double-reserved | unaffected | 409, "already being processed" | Client waits for the original to resolve |
| AI insufficient credits | Blocked before reservation | No `AIUsage` row created | Balance untouched | unaffected | `CREDITS_EXHAUSTED` state, upgrade CTA | User buys credits / upgrades |
| Payment success (webhook CAPTURED) | — | `Payment.CAPTURED`, idempotent event record | Not directly credit-related (subscription, not credit purchase, in this flow) | `ACTIVE` | Polling picks up new status | — |
| Payment failure | Provider declined/errored | `Payment.FAILED` | unaffected | Subscription stays `PENDING` (never activated on a failed payment) | Failure message shown | User can retry checkout (new `Payment` row) |
| Payment pending | Webhook not yet received | `Payment.CREATED`/`AUTHORIZED` | unaffected | `PENDING` | Client polls `/api/billing/payment-status/` | Resolves when webhook arrives or polling times out with a clear message |
| Webhook delay | Webhook arrives late | Event applied whenever it arrives; signature still verified | unaffected until applied | Transitions correctly once applied | Client-side polling bridges the gap | — |
| Duplicate webhook | Razorpay retries until 200 | Idempotency key blocks reprocessing | unaffected (no double-apply) | unaffected | No visible difference | — |
| Refund | Subscription refunded | `Payment.REFUNDED` | N/A (subscription, not credit, product) | Existing cancel-at-period-end semantics apply; a refund cannot resurrect an already-superseded subscription (verified in Step 6) | Reflects new status | — |
| Network failure (client) | `fetch()` rejects | No server-side change | No server-side change | No server-side change | `NETWORK_ERROR` state (status 0 sentinel) | Retry |
| Session expiry | Session invalid/expired | No user context | No change | No change | Redirected to login on next protected action | Re-login |
| OTP expiry | OTP window elapsed | `EmailOTP` row past `expires_at`, not deleted (audit trail) | N/A | N/A | "Code expired, request a new one" | Resend, subject to cooldown |
| OTP brute force | Too many wrong attempts | Attempt counter increments, row eventually locked | N/A | N/A | Generic lockout message (enumeration-safe) | Wait out the window / request new OTP |
| Profile update failure | Validation error | No partial write (atomic) | N/A | N/A | Field-level error shown | Retry with corrected input |
| History failure (delete/clear) | — | Scoped by `user=request.user`, can't affect another user's rows | N/A | N/A | Redirect back to history page | — |
| **Account deletion (Step 7 rework)** | N/A — success path only, no failure mode that partially anonymizes (wrapped in `transaction.atomic()`) | `User` anonymized+deactivated; `Payment`/`Subscription`/`CreditTransaction`/`AIUsage` preserved and still correctly attributed | Balance untouched (moot — login impossible) | Any live subscription cancelled, entitlement revoked | Logged out, redirected | Re-signup with a new account is possible (original email freed); the same account cannot be "undeleted" via any exposed endpoint — this is intentional (no self-service reactivation path was requested) |

## Not done in this pass (explicitly out of scope, flagged not hidden)

- No CSP (see PHASE_9_SECURITY_AUDIT.md).
- No dedicated load/perf testing was run — Step 14 was a code-level audit
  (query patterns, no obvious N+1 found in the two views inspected:
  `history_view`, `dashboard_view`), not a benchmark. If real production
  traffic later reveals a slow endpoint, that's a "make a specific
  measured fix" job, not something to guess at speculatively now (the
  brief's own Step 14 rule: "do not optimize prematurely").
- PostgreSQL concurrency was **not** re-run in this pass (no PostgreSQL
  instance available in this environment) — the existing test suite's
  concurrency tests (`test_credit_concurrency.py`,
  `test_subscription_concurrency.py`, `test_entitlement_concurrency.py`)
  ran against SQLite only, same as prior phases. Per the brief's own
  Step 16 rule, **this is explicitly recorded as not verified**, not
  claimed as done.
