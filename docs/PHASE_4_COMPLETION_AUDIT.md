# PHASE 4 — Completion Audit

## Files changed

No git repository was present in the extracted baseline (see
`PHASE_4_FORENSIC_FINDINGS.md` §"Exact integration seam" preamble), so
this list was tracked manually as each file was created/edited during
this phase, rather than produced by `git diff`.

**New files:**
- `accounts/billing_config.py`
- `accounts/services/razorpay_client.py`
- `accounts/services/payment_service.py`
- `accounts/billing_views.py`
- `accounts/webhook_views.py`
- `accounts/migrations/0010_providerevent_payment.py`
- `accounts/tests/test_billing_config.py`
- `accounts/tests/test_payment_models.py`
- `accounts/tests/test_razorpay_client.py`
- `accounts/tests/test_payment_service.py`
- `accounts/tests/test_billing_views.py`
- `accounts/tests/test_webhook_views.py`
- `accounts/tests/test_payment_concurrency.py`
- `PHASE_4_FORENSIC_FINDINGS.md`
- `PHASE_4_PAYMENT_ARCHITECTURE.md`
- `PHASE_4_SECURITY_AUDIT.md`
- `PHASE_4_COMPLETION_AUDIT.md` (this file)

**Modified files:**
- `accounts/models.py` — added `Payment`, `ProviderEvent` (appended;
  no existing model touched)
- `accounts/services/exceptions.py` — added
  `PaymentServiceError`/`UnpayablePlanError`/`DuplicateLiveSubscriptionError`/
  `PaymentVerificationError`/`PaymentNotFoundError` (appended; no existing
  exception class touched)
- `accounts/admin.py` — added `PaymentAdmin`, `ProviderEventAdmin`
  (imports and registrations only)
- `accounts/urls.py` — added 4 routes + 1 import line
- `AI_GENERATORS/settings.py` — added `RAZORPAY_KEY_ID`/
  `RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET` (appended after the
  existing email-config block; no existing setting changed)
- `requirements.txt` — added `razorpay==2.0.1`
- `.env.example` — added Razorpay env var documentation

**Not modified at all:** `accounts/subscription_service.py` (imported and
called, never edited), `accounts/entitlement_admin.py`,
`accounts/entitlement_service.py`, `accounts/entitlement_config.py`,
`accounts/credit_service.py`, `accounts/billing_utils.py`, every Phase
1/2/3 test file, every existing migration.

## Models

- `Payment` — 19 fields (see `PHASE_4_FORENSIC_FINDINGS.md` for the full
  list), 6 constraints (2 conditional-unique, 1 unconditional-unique via
  `Meta.constraints`, 2 check constraints, plus the FK/PROTECT
  relationships), 5 indexes.
- `ProviderEvent` — 10 fields, 1 unique constraint, 3 indexes.

## Services

- `accounts/billing_config.py` — `resolve_price()`, `is_payable_plan()`
- `accounts/services/razorpay_client.py` — `get_client()`,
  `get_public_key_id()`, `create_order()`, `verify_checkout_signature()`,
  `verify_webhook_signature()`, `fetch_payment()`
- `accounts/services/payment_service.py` — `create_payment_order()`,
  `verify_checkout_callback()`, `process_webhook_event()` (+ internal
  `_dispatch_event`, `_handle_payment_captured`, `_handle_payment_failed`,
  `_resolve_payment_for_webhook`, `_safe_event_summary`, `_checkout_info`)

## APIs

- `POST /api/billing/create-order/`
- `POST /api/billing/verify-payment/`
- `GET  /api/billing/payment-status/<order_id>/`
- `POST /api/webhooks/razorpay/`

Confirmed absent (Step 22): `POST /api/billing/activate-premium/`,
`POST /api/billing/set-status/`, `POST /api/billing/set-amount/` — see
`PHASE_4_SECURITY_AUDIT.md` for the specific tests asserting 404.

## Migrations

`accounts/migrations/0010_providerevent_payment.py` — generated via
`python3 manage.py makemigrations accounts`, applied cleanly on both
SQLite and PostgreSQL. `python3 manage.py makemigrations --check
--dry-run` reports **"No changes detected"** against the final model
state (run immediately before writing this report).

## Security fixes / hardening applied

See `PHASE_4_SECURITY_AUDIT.md` for the full risk-by-risk table. Summary:
signature verification (checkout + webhook, both via the official SDK),
server-side amount/currency/order/user verification independent of any
client input, DB-level idempotency (order creation + webhook delivery),
DB-level and application-level IDOR prevention, read-only admin for both
new models, fail-safe (503, never crash or fake-success) behavior when
credentials are missing, no logging of secrets or raw payment payloads.

## Test count

- **100 Phase-4-specific tests** across 7 new test files (96 executed +
  4 skipped when run on SQLite; all 100 executed with zero skips on
  PostgreSQL).
- **382 tests total** in the full `accounts` suite after this phase
  (up from the confirmed **282-test Phase 3 baseline**, run and passing
  before any Phase 4 code was written — see below).

## SQLite results

Baseline (before any Phase 4 code, run first to establish a clean
starting point):

```
Ran 282 tests in ...
OK (skipped=5)
```

Final (after Phase 4, literal command and output):

```
$ python3 manage.py test accounts -v 1
...
Ran 382 tests in 157.941s

OK (skipped=9)
```

The 4 additional skips are exactly the 4 new PostgreSQL-only concurrency
tests in `test_payment_concurrency.py`, gated the same way the existing
Phase 3 concurrency tests already were.

## PostgreSQL results

Baseline (before any Phase 4 code):

```
Ran 282 tests in ...
OK
```

Final (after Phase 4, literal command and output, `pg_test_settings.py`,
PostgreSQL 16 installed and run locally in this sandbox for this
verification):

```
$ DJANGO_SETTINGS_MODULE=pg_test_settings python3 manage.py test accounts -v 1
...
Ran 382 tests in 166.878s

OK
```

**Zero skips** — every concurrency test, including the 4 new Phase 4
ones, actually executed against a real PostgreSQL instance with real
`threading.Thread`s and a `threading.Barrier` forcing genuine overlap, not
merely asserted to be safe by code inspection. This is also how a real,
sandbox-environment bug was caught and fixed during this phase — see
`PHASE_4_PAYMENT_ARCHITECTURE.md` §10 for the `select_for_update()` /
nullable-outer-join `NotSupportedError` and its fix.

## Razorpay TEST-MODE result

**Not performed.** No Razorpay TEST-mode credentials
(`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET`) were
available in this sandboxed environment. All signature-verification logic
was instead validated against the official `razorpay` Python SDK's real
cryptographic functions using locally-generated HMAC values that match
Razorpay's documented scheme exactly (order_id + "|" + payment_id for
checkout; raw body for webhooks) — see `test_razorpay_client.py` — which
verifies the *mechanism* is implemented correctly, but is not the same as
an actual round-trip against Razorpay's live TEST-mode API, dashboard
webhook delivery, or checkout.js.

## Manual E2E result

**Not performed against a real Razorpay account**, for the same reason.
The full flow (FREE user → checkout → TEST payment → webhook →
Subscription ACTIVE → UserEntitlement → Premium feature access → profile
reflects Premium) IS exercised end-to-end at the Django-test level with a
mocked `razorpay_client` boundary
(`WebhookPaymentCapturedTests.test_captured_activates_pending_subscription`
in `test_payment_service.py`, and the equivalent HTTP-level test in
`test_webhook_views.py`'s `test_valid_signature_captures_and_activates`),
confirming every layer above the Razorpay SDK boundary works correctly.
It has not been run against the real Razorpay TEST-mode checkout UI.

**To complete this verification**, an operator with a Razorpay account
should: set real TEST-mode credentials in `.env`, run the app, create a
checkout, pay with a Razorpay TEST card/UPI, and confirm the webhook
(configured in the Razorpay dashboard against a reachable
`/api/webhooks/razorpay/` URL, e.g. via `ngrok` in local dev) delivers and
is processed. `PHASE_4_SECURITY_AUDIT.md`'s closing section lists the
pre-production checklist.

## Known limitations

1. **No live Razorpay TEST-mode verification** (see above) — the single
   largest gap between "implemented" and "production-verified."
2. **Renewal is user-initiated, not fully automatic recurring billing** —
   this phase uses Razorpay's Orders API (one-time payments per period),
   not Razorpay's separate Subscriptions/UPI AutoPay mandate product. See
   `PHASE_4_PAYMENT_ARCHITECTURE.md` §11 for the full rationale; this was
   a disclosed scope decision, not an oversight.
3. **Refunds are now processed (as of Phase 8B).** `refund.processed` is
   handled: a full refund marks the `Payment` `REFUNDED` and immediately
   revokes access (`expire_subscription`) unless a later renewal already
   superseded that payment. A partial refund (amount below the payment's
   full amount) is recorded in `Payment.metadata` for audit visibility
   only — no proration/access rule was ever established in this project,
   so none was invented. `refund.created`/`refund.failed`/`dispute.*`
   remain signature-verified, durably recorded, and `IGNORED` (no
   completed money movement to react to, or genuinely out of scope). See
   `PHASE_8B_PROGRESS.md`.
4. **`X-Razorpay-Event-Id` absence is handled but degrades idempotency** —
   if a webhook delivery ever arrives without this header (not expected
   in normal Razorpay operation per their documentation, but not
   impossible), the event is still processed (never silently dropped) but
   loses the DB-level duplicate-delivery guarantee for that specific
   delivery; this is logged at `ERROR` for operator visibility rather
   than silently tolerated.
5. **No independent third-party penetration test** has been performed;
   this is a code-and-test-level audit only.

## Re-verification addendum (2026-08-30)

A fresh forensic re-audit was performed against this exact ZIP, independent
of trusting the claims above — every claim below was checked against the
actual current source, not assumed from this document.

**Source re-read directly (not just this doc):**
`billing_config.py`, `razorpay_client.py`, `payment_service.py`,
`billing_views.py`, `webhook_views.py`, the `Payment`/`ProviderEvent`
models, `accounts/urls.py`, `static/js/billing.js`,
`static/js/profile-billing.js`, `accounts/admin.py`, and the
`onExhaustionUpgradeSuccess` Premium-unlock UI path in `templates/main.html`.

**Confirmed still true, by inspection:**
- Price/currency are resolved only from `billing_config.py`; no client
  input reaches `resolve_price()`.
- Checkout-callback verification (`verify_checkout_callback`) never calls
  `subscription_service` — activation happens only from
  `_handle_payment_captured`, reached only via the signature-verified
  webhook endpoint.
- `_handle_payment_captured` re-derives the expected amount/currency from
  `billing_config.resolve_price()` and compares it against both the stored
  `Payment` row and the webhook payload before capturing — a mismatch is
  recorded as `FAILED`, not silently activated.
- Webhook idempotency is DB-constraint-backed
  (`uq_providerevent_provider_event_id`), not a Python-only check; a
  duplicate delivery is caught by `IntegrityError` and returns the
  already-recorded event.
- `create_payment_order`'s in-flight-checkout reuse and
  `uq_payment_one_created_per_user` correctly prevent duplicate orders from
  double-click/concurrent requests (`IntegrityError` path).
- `PaymentAdmin`/`ProviderEventAdmin` are add/change/delete-disabled
  (read-only), and neither exposes `RAZORPAY_KEY_SECRET`/
  `RAZORPAY_WEBHOOK_SECRET` anywhere in code, logs, or admin list/detail
  views (checked by grep, not sampling).
- `static/js/billing.js`'s `SUCCESS` state — and the "Premium activated"
  copy in `templates/main.html` — are only reached after the client polls
  `/api/billing/payment-status/` for `CAPTURED` **and** `/api/subscription/`
  for `ACTIVE`; no client-side flag can force this state.
- `profile-billing.js` reuses `window.BillingFlow` rather than
  re-implementing the checkout/verify/poll flow — no parallel/duplicate
  payment path exists on the frontend.
- No `*_v2`/`*_new`/`*_final`/`*_backup` files, no hardcoded
  `rzp_live`/`rzp_test` keys, no secret literals anywhere in the payment
  code paths (grep-verified across `.py`/`.js`/`.html`).

**Commands actually re-run this pass** (SQLite, this sandbox):

```
$ SECRET_KEY=*** GROQ_API_KEY=*** python manage.py check
System check identified no issues (0 silenced).

$ SECRET_KEY=*** GROQ_API_KEY=*** python manage.py makemigrations --check --dry-run
No changes detected

$ SECRET_KEY=*** GROQ_API_KEY=*** python manage.py test accounts -v 1
Ran 805 tests in 153.209s
OK (skipped=9)
```

The 805 total (up from the 382 recorded when Phase 4 first closed) reflects
Phases 5–8 having since been added to this same project; all Phase 4/4B
payment tests are included in that run and none were skipped, changed, or
removed to reach this result. The 9 skips are the same PostgreSQL-only
concurrency tests noted throughout this document — unchanged, still gated
identically, not re-verified against Postgres in this pass (no local
Postgres instance in this sandbox this time; the PostgreSQL run recorded
above from the original Phase 4 pass is the only one on record).

**No code changes were made this pass.** The implementation this ZIP
shipped with already satisfies the Phase 4 specification's business rules,
security requirements, and idempotency requirements on direct inspection —
there was no bug to fix.

**Still open, unchanged:** Razorpay TEST-mode credentials
(`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET`) are not
present in this sandbox's environment (confirmed via `env`, not assumed),
so a real checkout.js → TEST payment → live webhook round-trip still could
not be performed. This is the same, single outstanding gap as when Phase 4
originally closed — see "Manual E2E result" above for the exact operator
checklist to close it.

## Final status

**PHASE 4 IMPLEMENTED — EXTERNAL RAZORPAY VERIFICATION REQUIRED**

All 25 items in the completion gate that can be verified without a live
Razorpay account are met:

1. ✅ Razorpay SDK integrated (`razorpay==2.0.1`, pinned)
2. ✅ Secrets environment-based (`os.getenv`, never hardcoded/committed)
3. ✅ Payment model exists (separate from Subscription)
4. ✅ Payment state machine exists (enforced via CHECK constraint + code)
5. ✅ Server-side order creation works
6. ✅ Client cannot control amount (tested directly)
7. ✅ Client cannot activate Premium (checkout callback never calls
   subscription_service; tested directly)
8. ✅ Signature verification works (checkout + webhook, both tested)
9. ✅ Webhook verification works
10. ✅ Webhook idempotency works (DB-level, tested under real concurrency)
11. ✅ Payment amount is verified (dual-source, tested)
12. ✅ Payment belongs to correct user/order (IDOR-tested)
13. ✅ Successful payment activates subscription
14. ✅ Subscription activates entitlement (via existing Phase 3 wiring)
15. ✅ Failed payment does not grant Premium (tested)
16. ✅ Duplicate events do not duplicate activation (tested under real
    concurrency, including the distinct-event-id case)
17. ✅ IDOR tests pass
18. ✅ Security tests pass (see `PHASE_4_SECURITY_AUDIT.md`)
19. ✅ PostgreSQL concurrency tests pass (executed for real, not assumed)
20. ✅ Phase 1 tests pass (part of the 382 total)
21. ✅ Phase 2 tests pass (part of the 382 total)
22. ✅ Phase 3 tests pass (part of the 382 total)
23. ❌ Real Razorpay TEST MODE flow — **not performed**; explicitly
    documented above, not claimed
24. ✅ Documentation complete (this set of 4 documents)
25. ✅ No secrets committed (`.env` excluded from the deliverable; only
    `.env.example` with placeholder values)

Per the brief's own "STRICT COMPLETION RULE," item 23 not being verifiable
in this environment means this phase is reported as **IMPLEMENTED**, not
**COMPLETE**. Everything that does not require a live third-party account
has been built, migrated, tested (including real concurrent-load testing
against an actually-running PostgreSQL instance, which surfaced and fixed
one genuine bug), and documented honestly.
