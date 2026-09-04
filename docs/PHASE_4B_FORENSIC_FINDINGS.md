# PHASE 4B — Forensic Findings

Audit performed by directly inspecting the code in
`AI_GENERATORS_PHASE4_IMPLEMENTED.zip`, not by trusting the prior phase's
self-report. Every row below reflects what the code actually did at the
start of this phase, before any PHASE 4B change.

## Feature matrix (BEFORE this phase)

| # | Feature | DB | Service | API | URL | Frontend | Connected | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | Pricing page | — | `billing_config.resolve_price` existed | — | `/plans/` existed | 43-line placeholder, "Coming soon" | No | **Missing** |
| 2 | Premium plan display | — | n/a | `/api/plans/` (features only, no price) | exists | not rendered | No | **Missing** |
| 3 | Buy Premium CTA | — | — | — | — | disabled `<span>` | No | **Missing** |
| 4 | Create Razorpay order | `Payment` model ✅ | `payment_service.create_payment_order` ✅ | `POST /api/billing/create-order/` ✅ | ✅ | none | No | **Backend only** |
| 5 | Razorpay Checkout | — | — | — | — | **zero JS files in project** | No | **Missing** |
| 6 | Payment processing state | — | — | — | — | none | No | **Missing** |
| 7 | Payment verification | ✅ | `verify_payment` ✅ | `POST /api/billing/verify-payment/` ✅ | ✅ | none | No | **Backend only** |
| 8 | Webhook confirmation | `ProviderEvent` ✅ | `_handle_payment_captured` (atomic) ✅ | `POST /api/webhooks/razorpay/` ✅ | ✅ | n/a (server-to-server) | Yes | **Complete** |
| 9 | Payment pending state | `Payment.Status.AUTHORIZED` ✅ | ✅ | `GET /api/billing/payment-status/<id>/` ✅ | ✅ | none | No | **Backend only** |
| 10 | Payment success state | ✅ | ✅ | ✅ | ✅ | none | No | **Backend only** |
| 11 | Payment failure state | `Payment.Status.FAILED` ✅ | ✅ | ✅ | ✅ | none | No | **Backend only** |
| 12 | Subscription status | `Subscription` model ✅ | `subscription_service` ✅ | `GET /api/subscription/` ✅ | ✅ | not consumed | No | **Backend only** |
| 13 | Profile plan display | — | ✅ (`entitlement_service`) | `profile_data` returned `entitlement`/`subscription` | ✅ | **1 line**: `accountType` text only | Partial | **Mostly missing** |
| 14 | Profile upgrade CTA | — | — | — | — | none | No | **Missing** |
| 15 | Credit/token balance | `CreditAccount` ✅ | `credit_service.get_balance_snapshot` ✅ | `GET /credits/balance/` ✅ | ✅ | **not in `profile_data`, not rendered** | No | **Backend only** |
| 16 | Token usage | `CreditAccount.lifetime_used` ✅ | ✅ | via balance endpoint | ✅ | none | No | **Backend only** |
| 17 | Usage percentage | derivable from balance fields | — | — | — | none | No | **Missing (frontend)** |
| 18 | Free-limit exhaustion | `entitlement_service` decisions ✅ | ✅ | `/api/entitlements/` ✅ | ✅ | AI endpoint returns 403/429, no upgrade CTA surfaced | Partial | **Backend only** |
| 19 | Premium benefits | `PlanFeatureRule` ✅ | ✅ | `/api/plans/` ✅ | ✅ | not rendered | No | **Missing** |
| 20 | Subscription management | `cancel_subscription`/`restore_subscription` ✅ | ✅ | `/api/subscription/cancel/`, `/restore/` ✅ | ✅ | none | No | **Backend only** |
| 21 | Browser E2E flow | n/a | n/a | n/a | n/a | n/a | n/a | **Not run this phase — see limitations** |

## Confirmed via direct inspection (not assumed)

- `find . -iname "*.js"` returned **zero results** anywhere in the project
  before this phase — no `static/` directory existed at all.
- `templates/plans.html` was 43 lines: a static card with
  `<span class="cta disabled">Coming soon</span>`, no server data, no API
  calls.
- `templates/profile.html` was 1222 lines but only one line
  (`document.getElementById("accountType").innerText = d.accountType`)
  consumed any billing-related field from `/profile/data/`, even though
  that endpoint already returned full `entitlement` and `subscription`
  objects.
- No `activate-premium` endpoint exists anywhere (verified via
  `accounts/urls.py` and `grep -rn "activate"` across `accounts/`) —
  correctly, per the project's own architecture docstring in
  `subscription_views.py`.
- `accounts/billing_config.py:resolve_price()` was already the single
  source of truth for price, but nothing public read it — no endpoint or
  template exposed a price value anywhere.

## Backend re-verified in Step 2 (not blindly trusted)

For each of the four billing endpoints, actually inspected (not assumed):
auth (`@login_required` on all four), CSRF (standard Django session CSRF,
consumed correctly by `static/js/billing.js`'s `X-CSRFToken` header),
request/response schema (read directly from `billing_views.py`), ownership
validation (`request.user` only, never a client-supplied id), idempotency
(`uq_payment_one_created_per_user` constraint), and the actual URL/method
(`accounts/urls.py`). All matched what the prior phase claimed. No API was
found broken or incomplete — all four were reused as-is, no
`/api/billing2/` or duplicate created.
