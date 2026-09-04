# PHASE 4 — Payment Architecture

## 1. High-level flow

```
FREE USER
   |
   v
POST /api/billing/create-order/            (accounts/billing_views.py)
   |  -> payment_service.create_payment_order()
   |     - resolves price server-side from accounts/billing_config.py
   |     - creates/reuses a PENDING Subscription (or the live one, for renewal)
   |     - creates local Payment(status=CREATED)
   |     - calls razorpay_client.create_order() -> Razorpay Order
   v
Razorpay Checkout (browser, checkout.js)   <- only key_id/order_id/amount/currency sent
   |
   v
POST /api/billing/verify-payment/          (accounts/billing_views.py)
   |  -> payment_service.verify_checkout_callback()
   |     - verifies HMAC signature (razorpay_client.verify_checkout_signature)
   |     - cross-checks amount/currency/order via razorpay_client.fetch_payment()
   |     - marks Payment AUTHORIZED  <-- NOT CAPTURED, does NOT touch Subscription
   v
Razorpay (server-to-server, asynchronous, may arrive before OR after the
above -- order is not guaranteed and both paths are written to tolerate
either arriving first)
   |
   v
POST /api/webhooks/razorpay/               (accounts/webhook_views.py)
   |  -> verifies X-Razorpay-Signature (razorpay_client.verify_webhook_signature)
   |  -> payment_service.process_webhook_event()
   |     - durably, uniquely records the event (ProviderEvent)
   |     - payment.captured: re-verifies amount/currency/order server-side,
   |       marks Payment CAPTURED, calls subscription_service.activate_
   |       subscription() or renew_subscription()
   |     - payment.failed: marks Payment FAILED, calls subscription_service.
   |       cancel_subscription() (first payment) or mark_past_due() (renewal)
   v
subscription_service.*  (EXISTING, Phase 3, unmodified)
   |
   v
Subscription -> entitlement_admin.sync_from_subscription() (EXISTING, unmodified)
   |
   v
UserEntitlement -> Premium AI access
```

**The webhook is the only path that can move a Subscription into ACTIVE.**
The checkout callback (`verify-payment`) exists purely so the browser can
show a trustworthy "payment received, confirming..." state instead of a
bare unverifiable redirect — it never calls `subscription_service` itself.
This directly satisfies the top-level brief's repeated requirement that
"the browser must never be the source of truth for payment success" and
"never activate Premium from a redirect alone."

## 2. Why Payment is not Subscription (Step 4)

`Subscription` answers "what is this user currently entitled to and until
when." `Payment` answers "did a specific transaction with Razorpay
happen, for how much, verified how." A subscription can outlive many
payments (renewals) and a payment can fail without ever becoming a
subscription state change (a first-time PENDING subscription whose only
payment fails is simply cancelled). Conflating the two would mean either
duplicating payment fields onto `Subscription` (bloating a model that
`entitlement_service` reads on every request) or losing the transaction
history entirely. They are linked via `Payment.subscription` (FK,
nullable only until the row leaves `CREATED`), not merged.

## 3. Plan price source of truth (Step 7)

`accounts/billing_config.py` — see its own module docstring for the full
rationale for why this is a new module rather than a field on `Plan`.
`payment_service.create_payment_order()` is the only function that calls
`billing_config.resolve_price()`, and it does so using the `plan_code`
resolved server-side from the authenticated `Plan` row — never from a
client-supplied amount. `accounts/billing_views.py`'s
`test_client_supplied_amount_is_ignored` test asserts this directly: a
request body containing `"amount": 1` has zero effect on the amount
actually sent to Razorpay.

## 4. Payment state machine (Step 5)

```
CREATED --------> AUTHORIZED --------> CAPTURED --------> REFUNDED
   |                   |
   +----> FAILED <-----+
```

- `CREATED`: local Payment + Razorpay Order exist; no checkout attempt
  confirmed yet.
- `AUTHORIZED`: the checkout callback's signature verified AND the
  Razorpay API cross-check (amount/currency/order) matched. This is a
  UX/audit waypoint, not an entitlement trigger.
- `CAPTURED`: the webhook's `payment.captured` event verified server-side
  (Step 17 checks) and was accepted. This is the ONLY status that ever
  triggers a `subscription_service` call.
- `FAILED`: either the webhook's `payment.failed` event, OR a
  `payment.captured` event whose amount/currency/order failed
  server-side verification (Step 17: mismatch never activates, and is
  recorded as `FAILED` with `failure_reason` set for audit, not silently
  dropped).
- `REFUNDED`: modeled (`Payment.Status.REFUNDED`, in the CHECK constraint,
  in `TERMINAL_STATUSES`) but **no code path sets it** in this phase — see
  "Known limitations" in `PHASE_4_COMPLETION_AUDIT.md`. `refund.*` webhook
  events are received, signature-verified, and durably recorded as
  `ProviderEvent.Status.IGNORED` (Step 32 note: an unhandled-but-legitimate
  event type is not an error) rather than acted on, per Step 20: "If actual
  refund processing is not part of this phase, DO NOT fake it."

`FAILED -> CAPTURED` is not reachable through any code path: once a
`Payment` row's status is in `Payment.TERMINAL_STATUSES`,
`_handle_payment_captured` returns immediately without mutating it (see
`payment_service.py`, the `if payment.status in Payment.TERMINAL_STATUSES`
branch), and a retried purchase after `FAILED` always creates a **new**
`Payment` row via a fresh `create_payment_order()` call rather than
resurrecting the old one.

## 5. Payment success vs. capture (Step 18)

Three distinct moments are deliberately not conflated:

1. **Checkout success** (browser-reported) — never trusted alone; see
   above.
2. **Signature-verified checkout callback** — `AUTHORIZED`. Proves the
   browser round-trip wasn't forged, cross-checked against Razorpay's own
   `payment.fetch()`, but is still just Razorpay's synchronous API
   response, not the durable, replay-safe webhook record.
3. **Webhook-verified capture** — `CAPTURED`. This is the state that
   drives `subscription_service`.

Because every Razorpay Order in this project is created with
`payment_capture: 1` (see `razorpay_client.create_order`), Razorpay
auto-captures successful payments immediately server-side — so in
practice `AUTHORIZED` and `CAPTURED` are usually seconds apart, not a
manual-capture gap the project has to operate. The state machine still
keeps them distinct so that "the browser told us it worked" and "Razorpay
confirmed it worked" can never be accidentally treated as the same
signal, even if a future change removed the auto-capture flag.

## 6. Idempotency (Step 9, Step 15)

Two independent idempotency mechanisms, at two different layers:

- **Order creation** (`uq_payment_one_created_per_user`, a partial unique
  DB constraint on `Payment(user)` where `status='CREATED'`): a
  double-click or browser retry on `create-order` either reuses the
  existing in-flight `Payment`+Razorpay Order (same plan/interval) or is
  rejected with 409 (different plan/interval — a genuine conflict, not
  silently resolved). Enforced by the database, not just a Python-level
  "check then act," and verified under real concurrent load against
  PostgreSQL (`test_payment_concurrency.py`).

- **Webhook delivery** (`uq_providerevent_provider_event_id`, plus
  `Payment.provider_event_id` as a second backstop at the Payment level
  for the case of two *distinct* event ids describing the same logical
  capture): `ProviderEvent.objects.create()` either succeeds (first time
  this `event_id` has been seen) or raises `IntegrityError` (a duplicate
  delivery), which `process_webhook_event()` catches and returns the
  existing row without re-dispatching. Also verified under real
  concurrent load against PostgreSQL.

## 7. Signature verification (Step 11, Step 13)

Two separate HMAC-SHA256 verifications, using two separate secrets,
implemented via the official SDK (never a hand-rolled comparison):

- **Checkout signature**: `client.utility.verify_payment_signature()`,
  keyed with `RAZORPAY_KEY_SECRET`. Verifies `razorpay_order_id +
  "|" + razorpay_payment_id`, signed with the API secret.
- **Webhook signature**: `client.utility.verify_webhook_signature()`,
  keyed with `RAZORPAY_WEBHOOK_SECRET` (a **different**, separately
  configured secret — confirmed via Razorpay's own documentation during
  this phase, not assumed). Verifies the raw request body bytes.

Both raise `SignatureVerificationFailed` (never return a bare boolean a
caller might ignore) on any mismatch, including malformed/missing input —
folded into the same fail-closed path as a genuine cryptographic mismatch.

## 8. Amount/currency/order/user verification (Step 17)

Enforced at two points, independently:

1. In `verify_checkout_callback()`, immediately after signature
   verification, against a fresh `razorpay_client.fetch_payment()` call
   (never the client-supplied values).
2. In `_handle_payment_captured()`, against
   `billing_config.resolve_price(payment.plan.code,
   payment.billing_interval)` **and** the locally stored `payment.amount
   /payment.currency` from when the order was created — i.e. the webhook
   payload's amount is checked against two independent server-side
   sources, not just one.

A mismatch at either point never activates anything; at the webhook stage
it is recorded as `Payment.Status.FAILED` with `failure_reason` set, and
logged at `ERROR` level for operator visibility (Step 24 security audit
trail).

## 9. CSRF exemption on the webhook (Step 13)

See `accounts/webhook_views.py`'s module docstring for the full
justification, summarized: Django's CSRF protection defends
session-cookie-authenticated browser requests from being forged by
another site the user's browser trusts. The webhook endpoint never
receives a session cookie and is never called by a browser; its entire
trust boundary is the HMAC signature, checked first, before anything else
in the request is used. A CSRF token could not make an unsigned request
trustworthy, and Razorpay has no mechanism to supply one.

## 10. Concurrency (Step 25/26)

All four required scenarios were implemented as `TransactionTestCase`s
using real `threading.Thread`s plus a `threading.Barrier` to force actual
overlap, gated to run only against PostgreSQL (`test_payment_concurrency.py`,
mirroring the existing `test_subscription_concurrency.py` pattern) since
SQLite does not meaningfully enforce `select_for_update()` row locking in
this project. **These were actually executed against a real PostgreSQL 16
instance during this phase** (not just written and left unverified — see
`PHASE_4_COMPLETION_AUDIT.md` for the literal command and result):

- Two simultaneous `create-order` requests → exactly one local `Payment`
  row.
- Two simultaneous webhook deliveries with the SAME `event_id` → exactly
  one `ProviderEvent` row, exactly one subscription activation.
- Two simultaneous webhook deliveries with DIFFERENT `event_id`s for the
  SAME underlying payment → still exactly one subscription activation
  (the `Payment.provider_event_id`/`status` backstop, independent of
  `ProviderEvent`'s own uniqueness).
- Two simultaneous `verify-payment` requests for the same order → no
  corrupted state, `Payment` ends in a single consistent `AUTHORIZED`
  state.

One real bug was found and fixed during this verification, not merely
during code review: `select_for_update()` combined with
`select_related("subscription")` in `_resolve_payment_for_webhook()`
raised `NotSupportedError` on PostgreSQL, because `Payment.subscription`
is nullable and Postgres refuses `FOR UPDATE` on the nullable side of an
outer join. Fixed with `select_for_update(of=("self",))`, which restricts
the row lock to the `Payment` table itself (locking `Subscription`
separately is unnecessary here — `subscription_service`'s own functions
already take their own lock on the `Subscription` row when they run).

## 11. Renewal model — a disclosed scope boundary

This phase implements **Razorpay Orders API** payments (one-time charges
per billing period), not Razorpay's separate Subscriptions/recurring-
mandate product (auto-debit via saved card/UPI AutoPay). A `PENDING`
subscription's first successful payment activates it; an `ACTIVE`
subscription's renewal is a fresh `create_payment_order()` call for the
same plan/interval, tied to the *existing* subscription rather than
creating a new one, whose `payment.captured` webhook calls
`subscription_service.renew_subscription()`. This means renewal in this
phase is **user-initiated** (the user or the frontend has to trigger a
new checkout near period end), not fully automatic recurring billing.
Automatic recurring billing via Razorpay's Subscriptions API/UPI AutoPay
mandates is a materially larger integration (a different Razorpay
product, different webhook events, mandate lifecycle management) that was
out of scope for this phase and is called out explicitly as a known
limitation in `PHASE_4_COMPLETION_AUDIT.md` rather than silently assumed
or partially faked.
