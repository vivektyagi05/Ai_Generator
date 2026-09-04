# PHASE 4B — End-to-End Flow

For each feature: Frontend → API → Service → DB → Provider → Response → UI.

## 1. Pricing display

```
templates/plans.html (rendered server-side)
  -> accounts.views.plans_page
    -> Plan/PlanFeatureRule (DB, existing catalog)
    -> accounts.billing_config.resolve_price(plan_code, interval)  [existing fn]
    -> entitlement_service.get_current_plan() + live Subscription lookup
  -> template renders real price, real features, real CTA state
```
No JS round-trip needed for the initial price — it's server-rendered, so
there's nothing to fake client-side.

## 2. Upgrade purchase (new purchase)

```
[data-upgrade-btn] click
  -> BillingFlow.upgrade({plan, billingInterval})
  -> POST /api/billing/create-order/          [existing endpoint, reused]
    -> billing_views.create_order_view
      -> payment_service.create_payment_order()
        -> billing_config.resolve_price()      (server-authoritative amount)
        -> razorpay_client.create_order()      -> Razorpay API
        -> Payment row created (status=CREATED)
  <- {key_id, order_id, amount, currency, plan, billing_interval}
  -> loadRazorpayScript() -> window.Razorpay(options).open()
  -> [user completes payment in Razorpay's own UI]
  -> handler(response) -> POST /api/billing/verify-payment/
    -> billing_views.verify_payment_view -> payment_service.verify_payment()
      -> razorpay_client signature check
      -> Payment status -> AUTHORIZED (not yet CAPTURED)
  <- 200
  -> state = WAITING_FOR_WEBHOOK, poll GET /api/billing/payment-status/<id>/
       [meanwhile, async] Razorpay -> POST /api/webhooks/razorpay/
         -> webhook_views.razorpay_webhook_view
           -> payment_service._handle_payment_captured()  [transaction.atomic]
             -> Payment.status = CAPTURED
             -> subscription_service.activate_subscription()
               -> Subscription.status = ACTIVE
  -> poll sees status=CAPTURED
  -> confirm via GET /api/subscription/ -> status=ACTIVE
  -> state = SUCCESS -> redirect to /profile/
```

## 3. Profile billing display

```
templates/profile.html DOMContentLoaded
  -> loadProfileData() -> GET /profile/data/
    -> accounts.views.profile_data
      -> entitlement_service.get_entitlement_summary()
      -> subscription_service.sync_subscription() (lazy expiry reconcile)
      -> credit_service.get_balance_snapshot()      [added this phase]
  <- {entitlement, subscription, credits, ...}
  -> window.ProfileBilling.render(data)  -> DOM update (plan, status,
     period, renewal, credits bar, usage %)
```

## 4. Subscription management (cancel / restore)

```
[Cancel at Period End] click -> confirm() dialog
  -> POST /api/subscription/cancel/         [existing endpoint, reused]
    -> subscription_service.schedule_cancellation()
  <- {cancel_at_period_end: true, current_period_end}
  -> banner shows "Premium remains active until <date>"
  -> loadProfileData() re-run -> UI reflects cancel_at_period_end=true

[Keep Premium] click
  -> POST /api/subscription/restore/        [existing endpoint, reused]
    -> subscription_service.restore_subscription()
  <- {cancel_at_period_end: false}
  -> loadProfileData() re-run
```

## 5. Credit exhaustion (existing backend behavior, not modified)

```
AI generation request -> POST /api/ai/
  -> entitlement/credit check (existing, Phase 1/2) -> 403/429 if exhausted
```
This phase did not touch `/api/ai/` or its enforcement — Step 13 of the
brief said to surface the existing backend response, not add a new block.
No frontend change was made here because the AI request flow (`main.html`)
was out of PHASE 4B's scope (billing/profile only) and already returns a
real error; adding an "Upgrade to Premium" CTA to that specific error
toast in `main.html` was not implemented this phase (see Known
Limitations in the Completion Audit).

## 6. Login return-URL

```
Anonymous -> /plans/ -> "Log in to upgrade" (href includes ?next=/plans/)
  -> /login/ (next carried via hidden field on submit)
  -> POST /login/ -> user_login() validates `next` against
     url_has_allowed_host_and_scheme -> redirect(next or "home")
  -> back on /plans/, now authenticated -> loadProfileData()-equivalent
     server render shows the real Upgrade CTA
```
