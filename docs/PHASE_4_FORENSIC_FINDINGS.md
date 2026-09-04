# PHASE 4 — Forensic Findings

This document records what was actually found in the Phase 3 baseline
(`AI_GENERATORS_PHASE3_COMPLETE.zip`, extracted project root
`AI_GENERATORS_EMAIL_FIXED/`) before any Phase 4 code was written, per the
"STEP 1 — FORENSIC AUDIT" instruction. Nothing below is guessed — every
claim was verified by reading the actual file or running the actual
command shown.

No git repository was present in the extracted project (`git status`
reports "not a git repository"), so there is no `git diff` for this phase;
the "files changed" list in `PHASE_4_COMPLETION_AUDIT.md` was instead
assembled by tracking every `create_file`/`str_replace` operation
performed during this phase.

## 1. Current subscription architecture (`accounts/models.py`, `accounts/services/subscription_service.py`)

- `Subscription` (Phase 3) has: `user` (OneToOne), `plan` (FK), `status`
  (`PENDING`/`ACTIVE`/`PAST_DUE`/`GRACE_PERIOD`/`CANCELLED`/`EXPIRED`),
  `billing_interval` (`MONTHLY`/`YEARLY`), `provider` (free-text, default
  `"manual"`), `provider_subscription_id`, `current_period_start/end`,
  `cancel_at_period_end`, `last_provider_event_id` (its own webhook-replay
  idempotency backstop), timestamps.
- `LIVE_STATUSES = (PENDING, ACTIVE, PAST_DUE, GRACE_PERIOD)`;
  `TERMINAL_STATUSES = (CANCELLED, EXPIRED)`.
- `uq_subscription_one_live_per_user`: a partial unique constraint
  (`status IN LIVE_STATUSES`) enforcing at most one live subscription per
  user at the DB level, not just in Python.
- `VALID_TRANSITIONS` is an explicit whitelist dict inside
  `subscription_service.py`; `_transition()` is the single choke point
  every state change passes through, and it raises `InvalidTransitionError`
  for anything not in the whitelist. `PENDING`, `PAST_DUE`, and
  `GRACE_PERIOD` all have `ACTIVE` as a valid next state.
- `SubscriptionAuditLog` is an append-only audit row created by
  `_transition()` on every change, carrying `event_type`, `performed_by`,
  `source`, `reason`, `metadata`. The admin (`SubscriptionAuditLogAdmin`)
  already disables change/delete.
- Public API surface used by Phase 4:
  `create_subscription(user, plan, billing_interval, provider, source,
  reason)`, `activate_subscription(subscription, source, reason,
  provider_event_id)`, `renew_subscription(subscription, source, reason,
  provider_event_id)`, `mark_past_due(subscription, source, reason)`,
  `cancel_subscription(subscription, source, reason)`. All of these
  already accept a `provider_event_id` idempotency parameter and already
  no-op safely on a repeated call with the same event id where
  applicable — Phase 4 does not need to add any new idempotency mechanism
  to `Subscription` itself.
- **Confirmed by reading the module docstring directly**: *"no payment
  provider is ever called from here"* — Phase 3 was deliberately built
  payment-agnostic. This is the seam Phase 4 plugs into.

## 2. Current plan architecture (`accounts/models.py`, `accounts/entitlement_config.py`)

- `Plan` (Phase 2): `code` (unique, e.g. `FREE`/`PREMIUM`), `name`,
  `is_active`, `display_order`. **No price field of any kind.**
- `entitlement_config.py`'s `PLAN_DEFINITIONS` dict maps plan codes to
  entitlement *capabilities* (credits, feature flags) — also no price.
- Conclusion used to drive Step 7: since neither Phase 2 model carries a
  price, and the brief explicitly says not to abuse `Subscription` as the
  price source, a new dedicated module (`accounts/billing_config.py`) was
  created rather than adding a price column to `Plan`. See that module's
  own docstring for the full rationale (mixing "what a plan unlocks" with
  "what it costs" would give two different concerns — read by
  `entitlement_service` and `payment_service` respectively — a single
  shared field).

## 3. Current entitlement flow (`accounts/services/entitlement_admin.py`, `entitlement_service.py`)

- `entitlement_admin.sync_from_subscription(subscription)` is the only
  writer of `UserEntitlement`, and it is already called from inside
  `subscription_service._transition()` on every subscription status
  change — i.e. Phase 3 already wired Subscription → Entitlement
  end-to-end. Phase 4 does not touch `UserEntitlement` or
  `entitlement_admin.py` at all; calling `subscription_service`'s existing
  functions from the new webhook handler is sufficient to reach Premium
  access.

## 4. Current profile flow

- No changes were needed to any profile-facing view/template for this
  phase; `accounts/billing_views.py`'s `payment_status_view` gives the
  frontend a poll target it can call after checkout to reflect the
  webhook-driven state without needing new profile-page wiring.

## 5. Existing payment-related code before Phase 4

- `accounts/billing_utils.py` existed but contains **no Razorpay
  integration** — confirmed by reading it directly (it does not import
  `razorpay`, define an `order_id`/`payment_id`/`signature` field, or call
  any payment provider). It is unrelated helper code and was not modified.
- No `Payment` model, no webhook endpoint, no `razorpay` import anywhere
  in the Phase 3 baseline — confirmed via `grep -ri razorpay` across the
  extracted project returning zero matches before Phase 4 changes.

## 6. Environment configuration (`AI_GENERATORS/settings.py`, `.env.example`)

- `SECRET_KEY` is loaded from `os.getenv` and the app **hard-fails at
  import time** if it's missing in a non-DEBUG context — an existing
  precedent for "fail loudly on missing critical secrets" that Phase 4's
  Razorpay settings deliberately do NOT copy (see
  `PHASE_4_PAYMENT_ARCHITECTURE.md` "Fail-safe configuration" for why a
  missing payment credential should degrade only the billing endpoints,
  not the whole site).
- Email config (`BREVO_API_KEY` etc.) is the closest existing precedent
  for a third-party-API credential loaded via `os.getenv` directly in
  `settings.py` and consumed by a small number of modules — Phase 4's
  `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET`
  follow the same pattern.

## 7. Security gaps found (pre-Phase-4)

- None applicable to payments, since no payment code existed yet. General
  observation: `accounts/urls.py` already consistently uses
  `@login_required` + resolving identity from `request.user` (never a
  client-supplied `user_id`) across every authenticated endpoint —
  `billing_views.py` follows the same convention.

## 8. Dependency status

- `requirements.txt` had no `razorpay` entry. `pip index versions
  razorpay` (run inside the sandbox) showed `2.0.1` as latest; installed
  and inspected directly (`razorpay.Client`, `razorpay.Utility`) to
  confirm the actual method names (`order.create`, `payment.fetch`,
  `utility.verify_payment_signature`, `utility.verify_webhook_signature`)
  before writing `razorpay_client.py` against them — not assumed from
  memory. Web search was additionally used to confirm Razorpay's current
  documented webhook idempotency mechanism
  (`X-Razorpay-Event-Id` header).

## 9. Exact integration seam

```
Razorpay
   |
   v
Payment (NEW, Phase 4)              <- accounts/services/payment_service.py
   |
   v
subscription_service.{activate_subscription,renew_subscription,
                       mark_past_due,cancel_subscription}   <- EXISTING, unmodified
   |
   v
Subscription (EXISTING, unmodified schema)
   |
   v
entitlement_admin.sync_from_subscription()   <- EXISTING, called automatically
                                                  by subscription_service, unmodified
   |
   v
UserEntitlement -> Premium AI access   <- EXISTING, unmodified
```

Phase 4 adds two new models (`Payment`, `ProviderEvent`), one new config
module (`billing_config.py`), one new SDK-boundary module
(`razorpay_client.py`), one new service (`payment_service.py`), two new
view modules (`billing_views.py`, `webhook_views.py`), four new URL
routes, and admin registrations for the two new models. It does not
modify `Subscription`, `Plan`, `UserEntitlement`, `entitlement_admin.py`,
`entitlement_service.py`, `credit_service.py`, or any Phase 1/2/3 test.
