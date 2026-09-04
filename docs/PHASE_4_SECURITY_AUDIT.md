# PHASE 4 — Security Audit

One row per risk listed in Step 24 of the brief. "Test(s)" names the
actual test function(s) exercising it — all were run and passed on both
SQLite and PostgreSQL (concurrency-specific tests: PostgreSQL only, see
`PHASE_4_COMPLETION_AUDIT.md` for the literal run output).

| Risk | Mitigation | Test(s) |
|---|---|---|
| **Secret leakage** | `RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET` read only inside `razorpay_client.py`, never logged, never included in any JSON response. `_checkout_info()` in `payment_service.py` is an explicit allowlist of response fields (`key_id`, `order_id`, `amount`, `currency`, `plan`, `billing_interval`) — there is no code path that could accidentally serialize a full settings object or SDK client into a response. | `test_response_never_contains_key_secret`, `test_get_client_never_exposes_secret_in_repr` |
| **Signature bypass** | Both checkout and webhook signatures verified via the official SDK's `Utility.verify_payment_signature`/`verify_webhook_signature` (HMAC-SHA256), never a manual string compare. Missing signature header on the webhook is rejected identically to an invalid one. | `test_invalid_signature_rejected`, `test_missing_signature_header_rejected`, `test_forged_signature_rejected`, `test_verify_checkout_signature_invalid_raises`, `test_verify_webhook_signature_invalid_raises` |
| **Amount manipulation** | Amount is resolved server-side from `billing_config.py` at order-creation time and never read from any request body afterward. Webhook capture re-verifies the payload amount against BOTH the locally stored `Payment.amount` AND a fresh `billing_config.resolve_price()` call before activating anything. | `test_client_supplied_amount_is_ignored`, `test_captured_with_wrong_amount_does_not_activate`, `test_amount_mismatch_from_remote_rejected` |
| **Currency manipulation** | Same dual-check as amount, applied to currency. | `test_captured_with_wrong_currency_does_not_activate` |
| **Order ID substitution** | Webhook resolves the local `Payment` strictly by `(provider, order_id)`, which is DB-unique. A forged `order_id` pointing at another user's real order fails signature verification first (the signature is computed over the genuine order/payment pair by Razorpay, not attacker-controlled), and even a signature-valid but mismatched `entity.order_id` vs the resolved `Payment.order_id` is checked explicitly in `_handle_payment_captured`. | `test_tampered_body_after_signing_rejected`, `test_verify_checkout_signature_tampered_order_id_rejected` |
| **Payment ID substitution** | `payment_id` is never trusted as an identifier for lookup — the local `Payment` is always resolved by `order_id` (server-controlled, created by us) first; `payment_id` is only ever *recorded onto* an already-resolved row after verification, and is additionally DB-unique per provider (`uq_payment_provider_payment_id`) so it cannot be attached to two different `Payment` rows. | `test_duplicate_payment_id_rejected` |
| **User ID spoofing** | Every billing endpoint resolves identity exclusively from `request.user` (Django session auth) — no endpoint accepts or reads a `user_id` field from any request body. | `test_other_users_order_id_returns_404_not_someone_elses_payment`, `test_other_user_cannot_read_payment_status` |
| **Subscription ID spoofing** | No billing endpoint accepts a subscription id at all; the subscription is always resolved server-side from `request.user`'s own live/new subscription inside `payment_service`. | (structural — no such input exists; see `billing_views.py`) |
| **Replay attack (checkout)** | A previously-valid `(order_id, payment_id, signature)` triple replayed against `verify-payment` after the `Payment` has already reached `CAPTURED`/a terminal status is a no-op (idempotent early-return), never re-processed or re-authorized. | `test_already_captured_payment_is_idempotent_noop` |
| **Webhook replay** | See "Duplicate webhook" below — the same mechanism covers both accidental redelivery and a captured/replayed request. | `test_duplicate_webhook_delivery_is_idempotent`, `test_duplicate_delivery_same_event_id_processed_once` |
| **Duplicate webhook** | `ProviderEvent(provider, event_id)` is DB-unique; a second delivery of the same `event_id` hits `IntegrityError` on insert and is returned as a no-op without re-dispatching, enforced at the database level (verified under real concurrent threads on PostgreSQL, not just sequential calls). | `test_two_concurrent_webhook_deliveries_same_event_id_activate_once` |
| **Duplicate order** | `uq_payment_one_created_per_user` (partial unique constraint) prevents two local `Payment` rows in `CREATED` for the same user; `create_payment_order()` reuses the existing in-flight order/local row on a repeat call. Verified under real concurrent threads. | `test_double_click_reuses_existing_created_payment`, `test_two_concurrent_create_order_calls_create_only_one_payment` |
| **Duplicate activation** | `_handle_payment_captured` checks `payment.provider_event_id == event_id or payment.status == CAPTURED` and returns immediately if either is true — a second, distinct event_id for the same already-captured payment cannot re-trigger `subscription_service`. | `test_second_distinct_captured_event_for_already_captured_payment_is_noop`, `test_two_concurrent_captured_events_different_ids_still_activate_once` |
| **Race conditions** | `select_for_update()` on the relevant `Payment`/`Subscription` rows inside every mutating `transaction.atomic()` block; DB uniqueness constraints as the ultimate backstop beyond Python-level locking. All four required concurrency scenarios (Step 25) actually executed against PostgreSQL, not merely reasoned about — see `PHASE_4_PAYMENT_ARCHITECTURE.md` §10 for the real `NotSupportedError` bug this caught. | `accounts/tests/test_payment_concurrency.py` (all 4 tests) |
| **CSRF mistakes** | The webhook is deliberately CSRF-exempt with an explicit, documented justification (no session cookie ever presented, signature is the trust boundary) — see `webhook_views.py` docstring and `PHASE_4_PAYMENT_ARCHITECTURE.md` §9. Every other billing endpoint (`create-order`, `verify-payment`, `payment-status`) keeps Django's default CSRF protection, same as the rest of the Phase 3 `accounts` app. | `test_no_csrf_token_required_endpoint_still_reachable` (confirms the webhook specifically bypasses CSRF, i.e. that the exemption works) |
| **IDOR** | Every read/write in `billing_views.py` filters by `user=request.user` at the ORM level (never fetch-then-check-ownership-after) — a nonexistent order and another user's real order both resolve to the identical 404, so existence is never leaked either. | `test_other_users_order_id_returns_404_not_someone_elses_payment`, `test_other_user_cannot_read_payment_status`, `test_nonexistent_order_id_returns_same_404_as_someone_elses` |
| **Open redirects** | No redirect is issued by any Phase 4 endpoint — all three billing views and the webhook view return JSON only. Not applicable to this phase's code. | (structural — grep confirms no `HttpResponseRedirect`/`redirect(` in `billing_views.py`/`webhook_views.py`) |
| **Logging sensitive data** | `logger.warning`/`logger.error` calls throughout `razorpay_client.py`/`payment_service.py`/`webhook_views.py` log only ids (`order_id`, `payment_id`, event type) via `extra={}`, never the secret, never a full raw webhook payload, never card/bank/UPI details. `ProviderEvent.payload_summary` is built by `_safe_event_summary()`, an explicit allowlist of non-sensitive fields — not the raw payload. | (structural — reviewed every `logger.*` call site in the three modules; `payload_summary`'s allowlist is directly inspectable in `payment_service._safe_event_summary`) |

## Forbidden endpoints (Step 22) — confirmed absent

`POST /api/billing/activate-premium/`, `POST /api/billing/set-status/`,
and `POST /api/billing/set-amount/` do not exist anywhere in
`accounts/urls.py` or any view module. Confirmed both by inspection and by
test: `test_no_activate_premium_endpoint_exists`,
`test_no_set_status_endpoint_exists`, `test_no_set_amount_endpoint_exists`
all assert a plain Django 404 (route not found) for these paths.

## Admin cannot casually mutate payment state (Step 31)

`PaymentAdmin` and `ProviderEventAdmin` (in `accounts/admin.py`) both
override `has_add_permission`, `has_change_permission`, and
`has_delete_permission` to return `False` unconditionally, and list every
model field under `readonly_fields`. The Django admin is therefore
strictly a read-only viewer for both models — there is no admin form
through which a staff user could mark a payment successful, edit an
amount, or flip a provider id. The only writer of these models anywhere in
the codebase is `accounts/services/payment_service.py`.

## Fail-safe configuration (Step 3)

Confirmed by test that every billing/webhook endpoint returns a clean 503
(never a 500 crash, never a fake success) when
`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET` are
unset: `test_missing_razorpay_config_fails_safely_503`,
`test_missing_webhook_secret_fails_safely`. No payment code path
constructs a Razorpay client with an empty/`None` credential.

## What this audit does not cover

This is a code-level and test-level audit run inside a sandboxed
development environment against Razorpay's official Python SDK's
documented behavior. It has **not** been exercised against Razorpay's
actual TEST-mode API (no credentials were available in this environment —
see `PHASE_4_COMPLETION_AUDIT.md` "Known limitations"), and has not been
through an independent third-party penetration test. Before handling real
money in production, an operator should additionally: rotate/generate
production credentials directly in the Razorpay dashboard (never reuse
anything from this development phase), configure the production webhook
URL and its own distinct `RAZORPAY_WEBHOOK_SECRET` in the dashboard, and
run the manual end-to-end TEST-mode flow described in
`PHASE_4_COMPLETION_AUDIT.md` §"Manual E2E" against real Razorpay TEST
credentials before going live.
