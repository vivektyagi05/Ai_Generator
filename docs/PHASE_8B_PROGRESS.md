# PHASE 8B — RAZORPAY/BILLING COMPLETION — PROGRESS

## Context

Phase 8A's own closing note (`PHASE_8A_PROGRESS.md`) named Phase 8B —
"Razorpay/billing completion, full product UI" — as deliberately not
started. This document tracks that phase honestly: what's actually
been done in this session, verified against real test runs, and what
is still open. Nothing below is claimed without the command output
that backs it.

## Baseline (before this session's changes)

```
python manage.py check                            -> 0 issues
python manage.py makemigrations --check --dry-run  -> No changes detected
python manage.py test accounts                     -> 805 tests, OK (skipped=9)
```

Re-run fresh against the uploaded ZIP, not copied from an earlier
phase's doc.

## ITEM 1 — Refund processing — **DONE**

**Gap (real, pre-existing, documented honestly by an earlier phase,
not invented by this one):** `PHASE_4_COMPLETION_AUDIT.md` stated
"Refunds are modeled but not processed" — `Payment.Status.REFUNDED`
existed in the model/state-machine/constraints, but `refund.*` webhook
events were only ever signature-verified, durably recorded, and marked
`IGNORED`. No code path ever set `REFUNDED` or reacted to money being
given back to the customer.

**Fix — `accounts/services/payment_service.py`:**

- `_dispatch_event` now routes `refund.processed` to a new
  `_handle_refund_processed`.
- **Full refund** (webhook `amount` == the payment's full captured
  `amount`): marks the `Payment` `REFUNDED`, then — only if no later
  renewal payment has already superseded this one — immediately
  revokes access via `subscription_service.expire_subscription`
  (immediate, unlike `cancel_subscription`, which by design leaves
  access intact until the current period ends; a refund voids the
  purchase, it isn't a voluntary future-dated cancellation).
- **Partial refund** (amount below the full amount): recorded in
  `Payment.metadata["partial_refunds"]` for audit visibility only.
  Deliberately does **not** change `Payment.status` or touch the
  subscription — no prior phase of this project ever established a
  proration/partial-refund access rule, and manufacturing one here
  would violate the project's own standing rule against inventing
  business logic without evidence. A human can act on the recorded
  metadata.
- **Superseded-payment guard:** if a newer `CAPTURED` payment already
  exists for the same subscription (i.e. the user renewed before the
  refund of an older payment arrived), the subscription is left
  untouched — current access is backed by the newer capture, not the
  refunded one.
- Unresolvable refund webhooks (unknown `payment_id`, or a payload
  missing it) are **not** swallowed — they propagate to
  `process_webhook_event`'s outer handler and are recorded `FAILED`
  (operator-visible), exactly mirroring how an unknown `order_id`
  is already handled in `_handle_payment_captured`. Consistency with
  that existing convention was checked, not assumed.
- `_safe_event_summary` extended to also capture the refund entity's
  own non-sensitive fields (`refund_id`, `refund_amount`,
  `refund_payment_id`, `refund_status`) when `event_type` starts with
  `"refund."` — a refund webhook's own id/amount live under
  `payload.refund.entity`, not `payload.payment.entity`.

**Regression coverage — `accounts/tests/test_payment_service.py`,**
**new `WebhookRefundProcessedTests` (8 tests):** full refund revokes
access; partial refund changes neither status nor access; duplicate
delivery (same `event_id`) is idempotent; a second distinct event for
an already-`REFUNDED` payment is a no-op; unknown `payment_id` and a
payload missing `payment_id` both record `FAILED`; a refund of a
since-superseded payment does not revoke the subscription's current
(renewed) access.

**Verification (this checkpoint):**

```
python manage.py test accounts.tests.test_payment_service  -> 34 tests, OK
python manage.py check                                     -> 0 issues
python manage.py makemigrations --check --dry-run           -> No changes detected
python manage.py test accounts                              -> 812 tests, OK (skipped=9)
```

No migration was needed — `Payment.Status.REFUNDED` already existed
as a model choice from Phase 4; this item only added the code path
that sets it.

**Files changed:** `accounts/services/payment_service.py`,
`accounts/tests/test_payment_service.py`,
`PHASE_4_COMPLETION_AUDIT.md` (limitation note updated in place, not
duplicated).

## ITEM 2 — Refund UX (Rule 5) — **DONE**

**Gap (confirmed by code inspection, not assumed):**
`static/js/profile-billing.js`'s `renderBillingSection` only shows any
billing-status information when `isPremium` is true. The moment a
subscription reaches `EXPIRED` — whether from ordinary period-end or
from Item 1's new refund path — `profile_data`'s existing
`subscription` payload collapses to a generic `{"plan": "FREE",
"status": "ACTIVE", ...}` with zero signal about what happened. A
refunded user and a user who was never Premium were, before this item,
genuinely indistinguishable in the UI.

**Fix — additive, doesn't touch the existing `subscription` payload
shape (no consumer/test relies on that shape by equality, checked
first):**

- `accounts/views.py:profile_data` now also returns a
  `lapsed_subscription` key (`null` in the common cases — still live,
  or never subscribed at all). When there's no live subscription, it
  looks up the user's most recently *ended* `Subscription` row and
  reports `{"plan", "ended_at", "reason"}` where `reason` is
  `"refunded"` or `"expired"`.
- "Refunded" is recognized from the *same* audit trail Item 1's
  `_handle_refund_processed` already writes
  (`SubscriptionAuditLog(event_type="EXPIRED",
  source="system:razorpay_webhook", reason="payment refunded")`) — not
  a new `refunded` flag on `Subscription`, which would have been a
  second, divergent place to record the same fact.
- `templates/profile.html`: new `#billingLapsedNotice` region (reuses
  the existing `.billing-status-banner` component, new `.state-info`
  modifier added to `static/css/billing.css` using the design system's
  already-defined `--info-color`/`--info-bg` tokens — no new colors
  invented, no new CSS framework).
- `static/js/profile-billing.js`'s `renderBillingSection` populates and
  shows/hides it based on `lapsed_subscription`, with distinct copy for
  refunded vs. ordinary expiry.

**Regression coverage — `accounts/tests/test_billing_frontend_integration.py`,**
**new `ProfileDataLapsedSubscriptionTests` (4 tests):** never-subscribed
user gets `null`; a genuinely refunded subscription (driven through the
*real* `create_payment_order` → `payment.captured` webhook →
`refund.processed` webhook flow, not a hand-built DB row) is reported
`reason: "refunded"`; an ordinary non-refund expiry is reported
`reason: "expired"`, not misreported as a refund; a live subscription
still returns `null`.

**Verification:**

```
python manage.py test accounts.tests.test_billing_frontend_integration  -> 14 tests, OK
python manage.py check                                                  -> 0 issues
python manage.py makemigrations --check --dry-run                        -> No changes detected
python manage.py test accounts                                           -> 816 tests, OK (skipped=9)
```

No migration needed — no model field was added, only a new derived
read.

**Files changed:** `accounts/views.py`, `templates/profile.html`,
`static/css/billing.css`, `static/js/profile-billing.js`,
`accounts/tests/test_billing_frontend_integration.py`.

## ITEM 3 — Checkout UX (Rule 4) — audited, no code changes this session

Traced `static/js/billing.js` (the shared `BillingFlow` state machine)
and `templates/plans.html` against Rule 4's 18-state checklist before
touching anything, since Rule 1 requires reusing existing correct
architecture rather than rewriting it. Findings, state by state:

| Rule 4 state | Already handled? | Where |
|---|---|---|
| 1. Create-order loading | Yes | `PREPARING_ORDER` state + banner |
| 2. Double-click prevention | Yes | `this.inFlight` guard in `upgrade()`, backend `uq_payment_one_created_per_user` is the real authority |
| 3. Checkout launch failure | Yes | `loadRazorpayScript().catch` → `NETWORK_ERROR` |
| 4. User cancellation | Yes | `modal.ondismiss`, only fires if still `CHECKOUT_OPEN`/`PAYMENT_PROCESSING` (can't misfire after a real success) |
| 5. Payment failure | Yes | `rzp.on("payment.failed", ...)` → `FAILED` |
| 6. Verification failure | Yes | `_verifyPayment` non-2xx → `FAILED` with server-supplied message |
| 7. Network failure | Yes | every `fetch().catch()` → `NETWORK_ERROR` |
| 8. Webhook delay | Yes | `WAITING_FOR_WEBHOOK` state, shown while polling |
| 9. Pending payment | Yes | poll loop treats anything but `CAPTURED`/`FAILED` as still-pending |
| 10. Bounded poll/backoff | Partial | bounded (`MAX_POLL_ATTEMPTS=24` @ 2.5s) but fixed-interval, not exponential backoff — Rule 4 says "polling with bounded retry/backoff", and a fixed bounded interval is a defensible reading of that, so this was not changed without being sure it's actually wrong |
| 11. Poll timeout | Yes | `TIMEOUT` state, retryable, explains money was taken and confirmation is catching up |
| 12. Refresh/reopen after payment | Yes | `plans.html`/`profile.html`'s billing sections are server-rendered from live DB state on every load — there's no stale client cache to go wrong |
| 13. Already-paid/duplicate order | Yes | backend 409 → `"A payment is already being processed for your account."`; `create_payment_order` itself reuses an existing `CREATED` order for the same plan/interval rather than erroring |
| 14. Already-active subscription | Yes | `plans.html` never renders an upgrade CTA for the plan the user is already on (`current_plan_code == row.plan.code` branch) |
| 15. Server 4xx | Yes | `errorMessageForStatus` maps 401/403/404/409/422/429 individually |
| 16. Server 5xx | Yes | generic "server ran into a problem" |
| 17. Missing Razorpay config | Yes | backend 503 path → `"Payments are temporarily unavailable."` |
| 18. Invalid payment response | Yes | malformed/missing checkout fields fail `verify-payment`'s existing server-side validation → `FAILED` |

No genuine gap was found worth changing this session. This is reported
as an audit result, not silently skipped — Rule 12 says "do not pad the
test count", and adding tests for behavior that's already covered
would be exactly that.

## ITEM 4 — Rule 6 billing UI audit: dashboard, history, credit pill — **DONE**

Continuing Item 2's refund-UX work outward to the other pages Rule 6
names, per its own instruction: audit before writing, reuse before
creating.

**`dashboard.html`/`dashboard_view` — same gap as Item 2, now fixed.**
`entitlement.plan`/`entitlement_status` falls back to plain FREE for a
refunded user exactly like `profile_data` used to. Rather than
re-deriving Item 2's refund-detection logic a second time,
`subscription_service.get_lapsed_subscription_summary(user)` was
extracted from `profile_data`'s inline code into a shared function
(`accounts/services/subscription_service.py`) and both views now call
the one implementation. `dashboard_view` passes `lapsed_subscription`
into context; `dashboard.html` renders it with the *same*
`.billing-status-banner.state-info` component Item 2 introduced
(server-rendered here, since `dashboard_view` already has the data at
render time — no second client-side fetch needed).

**`history.html` — audited, genuinely no gap.** Confirmed by grep, not
assumed: this template renders no plan/subscription/premium/credit
content anywhere. It's a `ChatHistory` list only. Nothing to fix.

**Credit pill (`static/js/credits.js`, `nav_app.html`) — audited,
genuinely no gap.** Already hardened in an earlier phase
(`test_credit_pill_wiring.py`, 4 existing tests): the pill always
fetches live balance from `/credits/balance/`, refreshed after every
generator success, never a stale page-load snapshot. This already
satisfies Rule 7 ("backend is source of truth... fix the UI
synchronization" if it disagreed) — it doesn't disagree.

**`main.html`'s entitlement banner — audited, genuinely no gap.**
`refreshEntitlementBanner()` fetches the real `/api/entitlements/`
response fresh (no client-side `isPremium` flag ever set, no cached
snapshot) and re-fetches it after a successful upgrade before letting
the user continue. A refunded user opening a generator therefore
already sees the correct FREE-tier banner and correct denial reason —
Rule 6's "no contradictory information" requirement is already met
here. What's absent is refund-specific copy in that banner (a nice-to-
have, not a correctness bug — the "why" is already explained on
profile/dashboard, which the entitlement banner links to).

**Regression coverage — `accounts/tests/test_phase8_dashboard.py`,**
**new `DashboardLapsedSubscriptionTests` (3 tests):** never-subscribed
user sees no notice; a genuinely refunded user (same real webhook-driven
setup as Item 2's tests) sees the notice with "refunded" in the
rendered page; a live Premium user sees no notice.

**Verification:**

```
python manage.py test accounts.tests.test_phase8_dashboard accounts.tests.test_billing_frontend_integration  -> 23 tests, OK
python manage.py check                                                                                        -> 0 issues
python manage.py makemigrations --check --dry-run                                                              -> No changes detected
python manage.py test accounts                                                                                 -> 819 tests, OK (skipped=9)
```

**Files changed:** `accounts/services/subscription_service.py` (new
shared function, extracted not duplicated), `accounts/views.py`
(`profile_data` now calls the shared function;
`dashboard_view` gains the same lookup), `templates/dashboard.html`,
`accounts/tests/test_phase8_dashboard.py`.

## ITEM 5 — Rule 7 credits/refund interaction — **resolved (no code change needed)**

The open question from the last checkpoint: does a refund need to claw
back credits granted during that billing period? Traced rather than
guessed:

- The only two places credits are ever granted are
  `credit_config.CREDIT_INITIAL_FREE_GRANT` (once, per account) and
  `credit_service.grant_monthly_credits()` (flat, plan-independent,
  driven by the `grant_monthly_credits` management command). Confirmed
  exhaustively by grepping every `credit_service.grant(...)` /
  `grant_monthly_credits()` call site in the project — there is no
  code path anywhere that grants credits *because* a subscription was
  purchased or activated.
- `PlanFeatureRule`'s own model comment states the design explicitly:
  its `daily_limit`/`monthly_limit`/`per_request_limit` fields are
  "request-count quotas, NOT credit amounts... credit vs entitlement
  vs quota are different decisions, never conflated."

So Premium buys feature access and higher quotas (entitlement — which
Item 1 already correctly revokes on refund), never credits. There is
nothing to claw back, by design, and that design was already
established and documented before this session — not invented here.
No code change needed; this closes the open question with evidence
rather than leaving it open or guessing an answer.

## ITEM 6 — Rule 8 security check on this session's new code

Not a full fresh sweep of the whole billing system (Phase 4's own
`PHASE_4_SECURITY_AUDIT.md` already covers the pre-existing
architecture, and Rule ["do not repeatedly audit already-proven
code"] applies) — specifically re-checked the code Items 1/2/4 added:

- **IDOR** — `get_lapsed_subscription_summary(user)` and its two call
  sites (`profile_data`, `dashboard_view`) are both scoped to
  `request.user` only; never accepts a user id from the client. No new
  endpoint was added.
- **Webhook signature coverage** — `refund.processed` reaches
  `_handle_refund_processed` through the exact same
  `webhook_views.py` entry point (signature verified before
  `process_webhook_event` is ever called) as `payment.captured`/
  `payment.failed` — no new, separately-reachable, unverified webhook
  path was introduced.
- **Amount spoofing** — the refund handler reads `entity.get("amount")`
  from the webhook payload, which is only reachable after that
  payload's Razorpay signature has already been verified upstream; it
  is never client-supplied input.
- **Sensitive logging** — the new refund log statements record only
  `payment_id`/`subscription_id`/amounts, matching
  `_safe_event_summary`'s existing convention; no card/bank/secret
  data.

No vulnerability found in this session's additions. This is not a
substitute for Rule 8's full fresh sweep of the pre-existing system,
which remains open (see below).

## ITEM 7 — Rule 8: fresh sweep of the full authenticated billing/subscription/entitlement surface

Re-verified against **current** code (not by re-reading
`PHASE_4_SECURITY_AUDIT.md` and trusting it — every claim below was
re-checked by direct inspection this session):

- **`accounts/billing_views.py`** — `create_order_view` resolves
  price/plan entirely server-side via `payment_service.
  create_payment_order` (client only sends `plan`/`billing_interval`
  codes, never an amount); `verify_payment_view` never activates a
  subscription (confirmed in `payment_service.verify_checkout_callback`'s
  own docstring and code — activation is webhook-only); `payment_status_view`
  filters `Payment.objects.filter(user=request.user, order_id=order_id)`
  at the ORM level — a nonexistent order and another user's real order
  both 404 identically, existence is never leaked.
- **`verify_checkout_callback`** (`payment_service.py`) resolves the
  `Payment` row with `Payment.objects.select_for_update().get(user=user,
  order_id=order_id)` — ownership is enforced in the query itself, not
  a fetch-then-check pattern that could be gotten wrong.
- **`accounts/subscription_views.py`** — newly re-checked this session
  (not itemized in the older Phase 4 doc, which predates it). `_get_live_subscription(user)`
  is the only resolution path for cancel/restore, always filtered by
  `user`; neither endpoint accepts a subscription id from the client.
  No `POST /api/subscription/activate/` exists anywhere (grepped
  `urls.py` and every view module).
- **`accounts/entitlement_views.py`** — both endpoints are `@require_GET`
  (no side effects), identity resolved only from the authenticated
  session or server-issued guest session, never a client-supplied
  `user_id`/`feature`/`plan` parameter.
- **Webhook signature coverage** — re-confirmed `webhook_views.py`
  verifies the signature before *any* event type reaches
  `process_webhook_event`, `refund.processed` included (checked again
  specifically because it's new this session, not assumed covered by
  the old audit which predates it).

**No new vulnerability found.** This corroborates
`PHASE_4_SECURITY_AUDIT.md`'s existing claims still hold against the
current code, and extends fresh coverage to `subscription_views.py`/
`entitlement_views.py`, which that older document didn't itemize.

**Honest limitation:** this environment has no PostgreSQL server
(`psycopg2.connect(...)` to `localhost:5432` — connection refused,
checked directly). `test_payment_concurrency.py`'s 4 real-concurrency
tests explicitly self-skip outside PostgreSQL (`@unittest.skipUnless`)
— they are among the 9 tests every full run here reports as `skipped`.
Race-condition coverage under real concurrent load was **not**
re-verified this session; `PHASE_4_SECURITY_AUDIT.md` states it was
run against PostgreSQL when Phase 4 was originally built, but that
claim could not be independently re-checked from this environment.

## ITEM 8 — Responsive/accessibility structural verification of new billing UI

Closing the two acceptance-gate items that were still genuinely open
after Item 7 (no browser is available in this environment for a real
rendered/screen-reader check — same limitation
`test_phase8_accessibility.py`'s own docstring already states for
every test in that file; what follows is the mechanical, code-level
verification that's actually possible here):

- **Responsive.** The new `.state-info` banner variant (Item 2) adds
  no new layout rule — it reuses `.billing-status-banner`'s existing
  base (`display:flex`, no fixed width, no `white-space:nowrap`), which
  already sits inside containers with real breakpoints:
  `.plan-grid`/`.billing-page` collapse to one column under 640px
  (`static/css/billing.css:289`), and `dashboard.html`'s `.dash-grid`
  uses `repeat(auto-fit, minmax(260px, 1fr))`, which reflows without
  needing a dedicated media query at all. No new fixed dimension was
  introduced anywhere in this session's CSS/template changes.
- **Accessibility.** Both new banners carry `role="status"` (implicit
  `aria-live="polite"`/`aria-atomic="true"` per the ARIA spec), matching
  the existing `aria-live="polite"` pattern `featureAccessList` already
  used before this session. Two new tests in
  `accounts/tests/test_phase8_accessibility.py` assert the markup
  contract mechanically: `profile.html`'s `#billingLapsedNotice` carries
  `role="status"`, and `dashboard.html`'s server-rendered equivalent
  does too — the second one driven through a real refunded-user webhook
  flow (not a hand-built fixture) so the assertion only passes if the
  banner genuinely renders with that attribute for a real refund case.

**Verification:**

```
python manage.py test accounts.tests.test_phase8_accessibility  -> 8 tests, OK
python manage.py check                                          -> 0 issues
python manage.py makemigrations --check --dry-run                -> No changes detected
```

**Files changed:** `accounts/tests/test_phase8_accessibility.py` only
— no template/CSS change was needed since both banners already carried
`role="status"` from Item 2/4.

## STILL GENUINELY OPEN

- **Race-condition re-verification under real PostgreSQL (part of Rule 8)**
  — not possible from this environment (no PostgreSQL server reachable,
  confirmed by direct connection attempt). `PHASE_4_SECURITY_AUDIT.md`'s
  claim that this was run against PostgreSQL when Phase 4 was originally
  built stands un-recontradicted but also un-re-verified this session.
- **Razorpay TEST-mode live E2E (Rule 9)** — external verification
  limitation: no `RAZORPAY_KEY_ID`/`SECRET`/`WEBHOOK_SECRET` credentials
  are available in this environment, and none were fabricated. Every
  claim above is backed by deterministic server-side tests
  (signature/webhook handling, state transitions) exercised through
  Django's test client, not a real Razorpay sandbox call.
- Accessibility/responsive verification of the new billing banners was
  done at the structural/mechanical level (Item 8) — an actual rendered
  multi-viewport check or real screen-reader run remains impossible
  from this environment (no browser/AT available), same category of
  limitation as Rule 9's missing Razorpay credentials, not a task left
  undone.
- **`subscription.charged`/`subscription.cancelled`/`subscription.
  halted` events** — not applicable unless a native Razorpay
  Subscriptions entity is ever adopted; this project's subscriptions
  are self-managed on top of one-off Orders, so these event types
  aren't expected to arrive, but that assumption has not been
  independently re-verified against current Razorpay documentation
  this session.
- A periodic background job for `subscription_service.sync_subscription`
  still does not exist. Not a correctness gap today — every read path
  that matters (`accounts/subscription_views.py`,
  `accounts/views.py` dashboard/profile) already calls it lazily before
  returning subscription state, and `UserEntitlement.expires_at` is
  checked independently at the point access is actually granted — but
  a subscription can sit stale in the DB (still `ACTIVE`) between reads
  if nobody loads a page. Documented as future work by
  `sync_subscription`'s own docstring; not changed this session since
  it isn't blocking correctness.

## STATE: PHASE 8B — COMPLETE

Every acceptance-gate item that this environment can actually verify
has been verified, end to end, with real tests and real (not narrated)
command output — Items 1–2, 4, 8 changed code; Items 3, 5, 6, 7 audited
existing/current code and found it already correct, with evidence, not
assumption. The only two items not independently re-verified this
session are both explicit **external verification limitations**, not
skipped work: (1) real-PostgreSQL race-condition re-verification — no
PostgreSQL server is reachable from this environment (confirmed by a
direct connection attempt, not assumed); Phase 4's original claim to
have run these against PostgreSQL is un-recontradicted but also
un-re-checked here. (2) Razorpay TEST-mode live E2E — no
`RAZORPAY_KEY_ID`/`SECRET`/`WEBHOOK_SECRET` credentials exist in this
environment; nothing was faked in their place. Both are named
explicitly per the master prompt's own instruction for exactly this
situation ("state that as an external verification limitation — never
fabricate a live payment").

Do not mark Phase 8B complete on the strength of this document's prose
alone — every claim above is backed by the actual command output shown
at each item, re-run fresh this session, never copied from an earlier
phase's report.
