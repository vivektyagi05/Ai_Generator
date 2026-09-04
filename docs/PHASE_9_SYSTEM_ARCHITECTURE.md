# PHASE 9 — System Architecture (snapshot)

This is a snapshot of the architecture as it stands after Phase 9, for a
reader who doesn't want to reconstruct it from 70+ prior phase docs.
Nothing below is new in Phase 9 except where explicitly marked.

## Apps / layout

- `AI_GENERATORS/` — Django project: settings, root urls, and
  `api_views.py` (the single shared AI-generation endpoint for all 6
  features: chat/code/story/application/proposal/photo).
- `accounts/` — everything else: auth/OTP, credits, entitlements,
  subscriptions, billing/webhooks, profile, history, dashboard.
- `accounts/services/` — the authoritative business-logic layer. Views
  are thin; every stateful mutation (credits, entitlements,
  subscriptions, AI usage, **account deletion — new in Phase 9**) goes
  through exactly one service module per domain, never duplicated.
- `templates/` + `static/` — server-rendered Django templates with
  mostly-inline JS (plus a handful of shared modules in `static/js/`:
  `api.js` for the shared fetch/CSRF/error-handling contract, `nav.js`,
  `toast.js`, `billing.js`, `profile-billing.js`, `credits.js`).

## Core domain model (money/state — the parts Phase 9 cared most about)

- `CreditAccount` (1 per user or guest session) ← `CreditTransaction`
  ledger rows. All mutation through `credit_service.py`
  (`grant`/`reserve`/`finalize`/`refund`), DB-level unique constraint on
  `(account, reference_id, transaction_type)` makes double-charge
  structurally impossible.
- `Subscription` ← `SubscriptionAuditLog`. State machine enforced in
  `subscription_service.py` (`VALID_TRANSITIONS` dict); drives
  `UserEntitlement` through `entitlement_admin.py`.
- `Payment` — one row per Razorpay order/attempt. Applied only through
  signature-verified webhook events (`webhook_views.py`), idempotent by
  provider event id.
- `AIUsage` — one row per generation request/attempt, `request_id`
  unique-constrained for idempotent replay; drives credit
  reserve/finalize/refund via `ai_usage_service.py`.
- `UserProfile` — avatar/phone/bio, plus **new in Phase 9**:
  `deleted_at`, the marker for the anonymize-on-deletion flow.

## Phase 9 additions

- `accounts/services/account_deletion_service.py` — the single
  authoritative account-deletion flow (anonymize + deactivate, not hard
  delete — see PHASE_9_PROGRESS.md Step 7 for the full rationale).
  Reuses `subscription_service.cancel_subscription()` rather than
  duplicating cancellation logic.
- `accounts/rate_limit.py` — extended with `check_login_throttle`/
  `record_failed_login` (Step 3), alongside the pre-existing generic
  `check_ip_throttle`/OTP-cooldown primitives.
- `AI_GENERATORS/settings.py` — `CACHES` now `REDIS_URL`-driven;
  `BEHIND_TLS_PROXY` opt-in flag for SSL redirect/HSTS.

## What Phase 9 deliberately did not change

No new services, no parallel implementations, no new generator
controller, no new CSS system, no custom `AUTH_USER_MODEL` (anonymization
uses the stock `django.contrib.auth.User`'s own fields rather than
introducing one). Every fix above extends an existing module in place.
