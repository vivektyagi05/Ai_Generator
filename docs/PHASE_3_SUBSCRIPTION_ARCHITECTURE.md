# PHASE 3 — SUBSCRIPTION & BILLING ARCHITECTURE

## 1. Domain model

```
Subscription
 ↓ (drives, via subscription_service.activate_subscription)
UserEntitlement          [Phase 2 — unchanged]
 ↓
Plan                     [Phase 2 — unchanged]
 ↓
PlanFeatureRule → Feature [Phase 2 — unchanged]
```

- **Subscription** answers "does this user have a commercial subscription,
  and under what billing terms?"
- **UserEntitlement** (Phase 2, untouched) answers "what can this user
  access?"
- **Plan** (Phase 2, untouched) answers "what does this subscription provide?"

A `Subscription` row existing does **not** by itself grant access. Only a
successful `subscription_service.activate_subscription()` call creates or
extends the `UserEntitlement` that `entitlement_service` actually reads.
Nothing in `entitlement_service.py` was changed — Phase 3 is additive.

### Fields and why each exists

| Field | Purpose |
|---|---|
| `user`, `plan` | Who, and access to what. |
| `status` | One of the six lifecycle states below — never a free string. |
| `billing_interval` | `MONTHLY` / `YEARLY` — drives period-length math. |
| `starts_at` | When the subscription was first created (audit/history). |
| `current_period_start/end` | The commercial billing window in force right now. `None` until the subscription is first activated. |
| `cancel_at_period_end` / `cancelled_at` | Distinguishes "scheduled to lapse" from "already ended" — access stays live until `current_period_end`. |
| `ended_at` | Set once, on the transition into CANCELLED or EXPIRED — final timestamp for the row's commercial life. |
| `entitlement` | Direct `OneToOne` link to the `UserEntitlement` this subscription drives — avoids parsing `source` strings to find it back. |
| `provider`, `provider_subscription_id`, `last_provider_event_id` | Phase 4 identity/idempotency hooks. Always blank/empty in every Phase-3-created row; never populated or read by any Phase 3 code path. |
| `metadata` | Free-form JSON for small facts that don't need their own column (e.g. `grace_period_ends_at`). |

Fields considered and deliberately **not** added: `provider_customer_id`
(no real use in Phase 3 without a payment provider), any pricing/amount
field (pricing belongs to a future billing/checkout domain, not this
subscription-state domain — Step 21 of the spec explicitly says not to
duplicate pricing here).

## 2. Status lifecycle

```
PENDING
 ├──► ACTIVE
 └──► CANCELLED

ACTIVE
 ├──► PAST_DUE
 ├──► CANCELLED           (cancel now, or cancel_at_period_end reaching its date)
 └──► EXPIRED             (period lapsed without renewal/cancellation)

PAST_DUE
 ├──► ACTIVE              (payment recovered)
 ├──► GRACE_PERIOD
 └──► EXPIRED

GRACE_PERIOD
 ├──► ACTIVE              (payment recovered during grace)
 └──► EXPIRED             (grace window elapsed)

CANCELLED
 └──► EXPIRED             (bookkeeping close-out, e.g. by a periodic sync)

EXPIRED                    (terminal — a new Subscription row is required
                             for any further commercial relationship)
```

This table is the single source of truth
(`subscription_service.VALID_TRANSITIONS`, a closed dict of frozensets).
`_assert_transition()` is called by every function that changes `status`;
there is no code path that sets `.status = X` without going through it.
`EXPIRED → ACTIVE` is intentionally absent — resurrecting a lapsed
subscription is a new `Subscription` row (Step 6 of the spec, "no silent
EXPIRED → ACTIVE").

`Subscription.LIVE_STATUSES` = `{PENDING, ACTIVE, PAST_DUE, GRACE_PERIOD}` and
`TERMINAL_STATUSES` = `{CANCELLED, EXPIRED}` are the two groupings the rest
of the codebase actually needs (e.g. "does this user have a live
subscription right now").

## 3. Billing period math

`accounts/billing_utils.py` is the only place that computes a period end
from a period start + interval. It:

- Uses `calendar.monthrange()` to clamp day-of-month to the last valid day
  of the target month (Jan 31 + 1 month → Feb 28, or Feb 29 in a leap year)
  rather than approximating a month as a fixed number of days.
- Computes yearly the same way (12-month add), so Feb 29 + 1 year correctly
  clamps to Feb 28 on a non-leap target year.
- Rejects naive datetimes outright (`NaiveDatetimeError`) rather than
  silently assuming a timezone.
- Renewals always advance from the **previous period's own end**
  (`renew_subscription`'s `anchor = subscription.current_period_end`), never
  from "now" — this is why a chain of monthly renewals starting on Jan 31
  lands on Feb 28 → Mar 28 → Apr 28, not back to the 31st each time. This is
  documented, expected behavior (see
  `test_billing_utils.test_renewal_chain_does_not_drift_across_months_with_varying_lengths`),
  not drift.

## 4. Cancellation

Two distinct operations, both server-authoritative and both requiring the
caller to already own the subscription (never a client-supplied
subscription id):

- **`schedule_cancellation`** (CANCEL_AT_PERIOD_END): sets
  `cancel_at_period_end=True` and `cancelled_at=now()`. Status stays
  `ACTIVE`. The entitlement is untouched — it keeps its existing
  `expires_at` (== `current_period_end`), so access naturally lapses exactly
  when the period ends, without a special-case check anywhere else in the
  codebase. `restore_subscription` reverses this (only while still `ACTIVE`
  with `cancel_at_period_end=True` — it cannot resurrect a terminal row).
- **`cancel_subscription`** (CANCEL_NOW): moves straight to `CANCELLED`,
  sets `ended_at`, and immediately calls `entitlement_admin.revoke_entitlement`
  if the linked entitlement is still active.

## 5. Expiration — deterministic, not cron-dependent

`subscription_service.get_effective_status(subscription, at=None)` derives
what a subscription's status *should* be purely from
`current_period_end`/`grace_period_ends_at` timestamps vs. `at`, without
mutating anything — mirroring `UserEntitlement.effective_status()`'s
existing pattern exactly. `sync_subscription()` reconciles the stored
`status` to that effective status (calling `expire_subscription` if it's
lapsed) and is called lazily by both `GET /api/subscription/` and
`profile_data`, so a read is always fresh even if no periodic job has run
yet. A future periodic job can call `sync_subscription` on all live rows,
but the deterministic derivation is what actually matters (Step 12: "must be
able to derive effective state from timestamps").

## 6. Subscription ↔ Entitlement integration

`activate_subscription()`:

1. Computes/extends the billing period.
2. Calls `entitlement_admin.grant_entitlement(user, plan, expires_at=period_end,
   source=f"subscription:{subscription.pk}")` — reusing the exact Phase 2
   function, which already handles "extend existing ACTIVE row" vs. "create
   new row" and already writes the `EntitlementAuditLog` row. Nothing here
   duplicates that logic.
3. Links `subscription.entitlement` to whatever `UserEntitlement` came back.

`renew_subscription()` re-calls the same `grant_entitlement` with the new
`expires_at` — same extend-in-place behavior, so renewing never creates a
second entitlement row.

`cancel_subscription()` / `expire_subscription()` call
`entitlement_admin.revoke_entitlement` on the linked entitlement.

**No client-facing activate endpoint exists.** The only ways a subscription
reaches `ACTIVE` are: (a) an admin action in Django admin (staff-only,
authenticated, audited via Django's own action log plus
`SubscriptionAuditLog`), or (b) in Phase 4, a verified payment webhook
calling this same `activate_subscription()` function. Both are equally
subject to the state machine and idempotency rules above — there is no
"trusted" shortcut.

## 7. Idempotency

- **Activation/renewal**: `provider_event_id` is compared against
  `Subscription.last_provider_event_id` before any mutation; a repeat is a
  no-op that returns the (unchanged) subscription.
- **Activation without an event id**: already-`ACTIVE`-with-a-period is
  treated as a no-op too (covers admin double-clicks / retried Phase 4
  webhook calls that don't carry an event id).
- **Cancellation/expiration**: already-terminal in the target state is a
  no-op (`if subscription.status == Status.CANCELLED: return subscription`).
- **Creation**: the true backstop against a duplicate live subscription is
  the DB-level `uq_subscription_one_live_per_user` constraint, not the
  Python `select_for_update()` pre-check alone — `select_for_update()`
  cannot lock a row that doesn't exist yet, so two concurrent
  `create_subscription()` calls can both pass the Python check and race to
  `INSERT`. `create_subscription()` catches the resulting `IntegrityError`
  and translates it into the same `DuplicateSubscriptionError` the Python
  check raises, so callers see one consistent exception either way. This
  was found and fixed via the real-PostgreSQL concurrency test in
  `test_subscription_concurrency.py` — see `PHASE_3_COMPLETION_AUDIT.md`.

## 8. Concurrency

Every state-mutating function opens `transaction.atomic()` and re-fetches
the subscription with `select_for_update()` before checking/changing status
— the in-memory object passed in by the caller is never trusted as
still-current. `Subscription.LIVE_STATUSES`-scoped queries in the views
layer (`_get_live_subscription`) don't need locking since they're read-only.

Verified against real PostgreSQL (see completion audit): concurrent
`create_subscription`, `renew_subscription`, and `cancel_subscription` calls
never produce duplicate live subscriptions, double-extended periods, or
double-processed cancellations.

## 9. Security (Step 25)

- No endpoint accepts `plan`, `status`, `provider`, `expires_at`,
  `current_period_end`, or `cancel_at_period_end` from the client body —
  `subscription_cancel_view` only reads `immediate: bool`; every other field
  in the JSON response is server-computed.
- No `subscription_id` / `user_id` parameter exists on any endpoint — every
  lookup is `Subscription.objects.filter(user=request.user, ...)`, scoped to
  the authenticated session, exactly like every other endpoint in this
  project.
- `GET /api/subscription/` never returns the subscription's internal PK,
  `provider`, or `provider_subscription_id`.
- Verified by `test_subscription_security.py`: plan/status/expiry spoofing
  via request body, cross-user read, cross-user cancel, and confirming no
  `/api/subscription/activate/` endpoint exists (404).

## 10. API contracts

```
GET  /api/subscription/
  -> {"plan": str, "status": str, "billing_interval": str|null,
      "current_period_start": iso|null, "current_period_end": iso|null,
      "cancel_at_period_end": bool}
  Anonymous -> redirected to login (django login_required default).
  No live subscription -> FREE-fallback shape (status "ACTIVE", everything
  else null/false).

POST /api/subscription/cancel/    body: {"immediate": bool (default false)}
  -> same shape as GET, reflecting the new state. 404 if no live
  subscription belongs to the caller. 409 on an invalid transition.

POST /api/subscription/restore/   body: {}
  -> same shape. 404 / 409 as above. Only reverses a scheduled
  cancel-at-period-end; cannot resurrect a terminal subscription.
```

`profile_data` (`/profile/data/`) gained a `"subscription"` key with the
same shape as the GET response above, computed via the same
`sync_subscription` path — never a second, divergent resolution.

## 11. Future Razorpay integration boundary (Phase 4)

Everything Phase 4 needs is already in place and untouched by Phase 3 logic:

- `Subscription.provider` / `provider_subscription_id` — set these when a
  Razorpay subscription is created; `provider_subscription_id` is unique,
  so this is the join key from a Razorpay webhook back to a local row.
- `Subscription.last_provider_event_id` — pass Razorpay's event id as
  `provider_event_id` into `activate_subscription`/`renew_subscription` for
  free idempotent replay handling.
- `mark_past_due` / `enter_grace_period` — call these from a
  `payment.failed` webhook handler; `enter_grace_period(grace_days=...)` is
  already configurable per call.
- No Phase 3 code imports `razorpay`, calls out to any payment provider, or
  creates a payment order/capture/webhook — confirmed by grep across
  `accounts/billing_utils.py`, `accounts/services/subscription_service.py`,
  `accounts/subscription_views.py`, `accounts/admin.py` (Subscription
  section only).

## 12. Known limitations

- **Plan-change (upgrade/downgrade)** is not implemented — the data model
  and service boundaries support it (a plan change is "settle the old
  subscription to a terminal state, create a new one"), but no
  upgrade/downgrade endpoint or proration logic exists yet (Step 15: this
  was explicitly out of scope for Phase 3).
- **`uq_subscription_one_live_per_user`** allows at most one live
  subscription per user across *any* plan, not per `(user, plan)` — this is
  stricter than the spec's literal wording ("prevent duplicate active
  subscriptions for the same user/plan") but was a deliberate simplification
  since this project only has one paid plan (PREMIUM) today; a future
  multi-tier-Premium plan change would need this constraint relaxed
  alongside adding real upgrade/downgrade support.
- **No periodic background job exists yet** to proactively call
  `sync_subscription()` on all live rows — reads are always correct (Step
  12's determinism requirement is met), but a subscription whose period
  lapsed and which nobody has since read via `/api/subscription/` or
  `/profile/data/` will sit in `ACTIVE` in the DB until it's next read. This
  is explicitly allowed by the spec ("If background processing is needed
  later, it should synchronize state, not be the only source of truth") but
  is worth wiring up a management command for in Phase 4.
- **Grace period deadline** is stored in `Subscription.metadata` (a JSON
  blob) rather than its own column, to avoid a schema change for what's
  currently a single, rarely-used field — worth promoting to a real column
  if Phase 4 ends up reading it from more than one place.
