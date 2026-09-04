# PHASE 4B — Frontend Architecture

## Stack decision

The project is Django templates with vanilla JS (no React/Vite anywhere in
this repo, confirmed by inspection). PHASE 4B follows that — no new
frontend framework introduced.

## New files

```
static/
  css/
    billing.css            -- shared styles for pricing + profile billing card
  js/
    billing.js              -- window.BillingFlow: the payment state machine
    profile-billing.js       -- window.ProfileBilling: profile card rendering
                                 + subscription management actions
```

`AI_GENERATORS/settings.py` gained one line:
`STATICFILES_DIRS = [BASE_DIR / 'static']` — required because no app had
its own `static/` directory and none was configured; without it Django's
staticfiles finder cannot see these files at all (confirmed: `collectstatic`
found 0 relevant files before this change, 3 after).

## `window.BillingFlow` (static/js/billing.js)

One centralized state machine, instantiated per purchase attempt via
`window.BillingFlow.create({ onStateChange, onSuccess })`. States exactly
as specified:

```
IDLE -> PREPARING_ORDER -> CHECKOUT_OPEN -> PAYMENT_PROCESSING
     -> VERIFYING_PAYMENT -> WAITING_FOR_WEBHOOK -> SUCCESS
                                                  \-> FAILED / TIMEOUT
CHECKOUT_OPEN -> CANCELLED (modal dismissed)
any network failure -> NETWORK_ERROR
```

Design choices and why:

- **In-flight guard** (`this.inFlight`): `upgrade()` is a no-op while a
  purchase is already running, so a double-click or double-Enter can only
  ever produce one `create-order` call from a given page load. The
  backend's `uq_payment_one_created_per_user` constraint is the real
  authority against a second tab or a client bug — this is a UX nicety on
  top of that, never a replacement for it.
- **Checkout success ≠ activation** (Phase 6/7): `handler()` on the
  Razorpay options object moves the state to `VERIFYING_PAYMENT`, which
  calls `verify-payment`. A 200 from `verify-payment` moves to
  `WAITING_FOR_WEBHOOK`, not `SUCCESS` — it only confirms the signature
  and that Payment left `CREATED`. Only a `CAPTURED` status from
  `payment-status` polling, **and** a subsequent `GET /api/subscription/`
  showing `status: "ACTIVE"`, produces `SUCCESS`.
- **Bounded polling everywhere**: main poll loop caps at 24 attempts /
  ~60s (`MAX_POLL_ATTEMPTS`), the post-CAPTURED subscription-confirmation
  loop caps at 5 attempts / ~5s (`MAX_CONFIRM_ATTEMPTS`). Neither can spin
  forever; both fall through to a terminal state (`TIMEOUT` or a
  `SUCCESS` with `activationPending: true`, respectively).
- **Error messages** (`errorMessageForStatus`): maps HTTP status to a
  plain-language string, prefers the backend's own `error` field when
  present, never surfaces a traceback or Razorpay internals.

## `window.ProfileBilling` (static/js/profile-billing.js)

Pure rendering + action-wiring layer over the `/profile/data/` payload and
the existing `/api/subscription/cancel|restore/` endpoints. Deliberately
thin — it does not compute anything the backend didn't already provide
(plan name casing aside, which is display formatting, not a business
value). Reuses `window.BillingFlow` for the Upgrade button rather than
re-implementing checkout.

## Backend touch points (all additive, no existing behavior changed)

1. `accounts/views.py:profile_data` — added a `credits` key to the JSON
   response, sourced from the existing `credit_service.get_balance_snapshot()`
   (same function `credit_balance` already used). No second ledger.
2. `accounts/views.py:plans_page` — extended to also resolve
   `billing_config.resolve_price()` per payable plan and the caller's live
   plan/subscription state, and pass both into the template context.
3. `accounts/views.py:user_login` — added a validated `next` param
   (`django.utils.http.url_has_allowed_host_and_scheme`) so an anonymous
   visitor who clicked Upgrade returns to `/plans/` after logging in,
   rather than always landing on `home`.

No new models, no new services, no new billing/credit/subscription APIs.
