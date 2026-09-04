# PHASE 4B — Security Audit

Scope: the frontend added this phase, plus the three small backend edits
(`profile_data`, `plans_page`, `user_login`). The Phase 4 payment backend
itself (`payment_service.py`, `webhook_views.py`, `razorpay_client.py`)
was **not modified** and its own security properties (verified in Step 2
of the forensic findings) are unchanged.

## Client-controlled values — verified none are trusted

Grepped every new `fetch(...)` call body:

- `POST /api/billing/create-order/` body: `{plan, billing_interval}` only
  — no amount, no user id. `payment_service.create_payment_order()`
  resolves the amount itself via `billing_config.resolve_price()`;
  confirmed by reading that function, it ignores any amount even if one
  were sent.
- `POST /api/billing/verify-payment/` body: `{order_id, payment_id,
  signature}` — exactly the three fields Razorpay's own checkout.js
  callback returns, nothing else. No status field is sent.
- `POST /api/subscription/cancel|restore/` body: `{}` / `{immediate}` —
  no plan/status field. Identity is `request.user` from the session,
  never a client-supplied id (confirmed in `subscription_views.py`, not
  changed this phase).
- Activation is never asserted client-side. `grep -rn "SUCCESS"
  static/js/billing.js` shows the only path to the `SUCCESS` state
  requires (a) `payment-status` returning `CAPTURED` **and** (b)
  `/api/subscription/` returning `ACTIVE` — both server reads, occurring
  after the purchase, never before.

## CSRF

All three POST call sites (`create-order`, `verify-payment`,
`subscription/cancel`, `subscription/restore` — 4 total; grep above found
3 distinct `method: "POST"` locations because `verify-payment` and
`create-order` are both in `billing.js`) send `X-CSRFToken` read from
`window.BillingFlow.getCSRFToken()`, which reads the `csrf-token` meta tag
first and falls back to the `csrftoken` cookie — same pattern already
used elsewhere in this codebase (`profile.html`'s existing
`getCSRFToken()`).

## XSS

`grep -rn "innerHTML" static/js/*.js templates/plans.html` → exactly one
use, in `plans.html`'s interval-toggle handler, and the interpolated
value is a `data-*-paise` integer attribute rendered by Django from
`billing_config.resolve_price()` — never user input, never a value read
from a fetch response. `profile-billing.js` uses `textContent`
exclusively for every dynamic value (plan name, dates, credit numbers).
No `eval`, no `new Function(...)`, no dynamic `<script>` injection other
than the one intentional, fixed-URL Razorpay SDK load
(`https://checkout.razorpay.com/v1/checkout.js`, hardcoded, not
constructed from any input).

## Secrets

`RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` — grepped
`static/js/*.js` and `templates/plans.html`/`profile.html`: neither
string appears. The only Razorpay value sent to the browser is `key_id`,
already public-safe by Razorpay's own design (it's meant to be embedded
client-side), and it only reaches the browser via
`create_order_view`'s existing response — unchanged this phase.

## Double-submit / duplicate payment

- Frontend: `BillingFlow.inFlight` blocks a second `upgrade()` call while
  one is in progress (covers double-click, double-Enter, same-tab retry).
- Backend (unchanged, re-verified): `uq_payment_one_created_per_user`
  constraint on `Payment` is the real authority — confirmed by reading
  `accounts/models.py` and `accounts/tests/test_payment_concurrency.py`,
  neither touched this phase. A second tab or a client bypassing the
  frontend guard still cannot create two `CREATED` payments for the same
  user; `create_order_view` surfaces that as a 409, which
  `errorMessageForStatus` renders as "A payment is already being
  processed for your account."

## IDOR

- `payment-status/<order_id>/` — not re-audited this phase (existing
  endpoint); `accounts/tests/test_billing_views.py` already covers
  cross-user access and was not modified, and it still passes.
- `/api/subscription/`, `/cancel/`, `/restore/` — all resolve the
  subscription via `request.user`, never accept a subscription id from
  the client (unchanged, `subscription_views.py`).

## Login redirect (new this phase)

`user_login`'s new `next` handling uses
`django.utils.http.url_has_allowed_host_and_scheme(candidate,
allowed_hosts={request.get_host()}, require_https=request.is_secure())`
— rejects any absolute URL to a different host. Covered by
`test_login_rejects_external_redirect_target` in the new test file
(passes).

## Not in scope this phase / unable to verify here

- Rate-limiting on the billing endpoints themselves (429 handling exists
  in the frontend's `errorMessageForStatus`, but whether the backend
  actually rate-limits `create-order`/`verify-payment` was not
  re-audited — that's Phase 4 backend territory, unchanged).
- Real Razorpay webhook replay/signature attack testing — this sandbox
  has no network path to Razorpay; `test_webhook_views.py`'s existing
  signature/replay tests were run (still pass) but no new webhook attack
  scenarios were added since the webhook handler itself wasn't touched.
