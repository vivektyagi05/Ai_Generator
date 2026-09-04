# PHASE 4B — Manual E2E Checklist

This sandbox has no network route to `checkout.razorpay.com` /
`api.razorpay.com` and no browser automation tool, so the real
Razorpay-TEST-mode and browser flows below were **not** executed by me —
only your own environment can do that. Everything up to the webhook is
mocked/verified via Django's test client instead (see
`PHASE_4B_COMPLETION_AUDIT.md`).

Run this in your own dev environment with real TEST credentials.

1. **Configure TEST credentials** — in `.env` (copy from `.env.example`):
   `RAZORPAY_KEY_ID=rzp_test_...`, `RAZORPAY_KEY_SECRET=...`. Get a
   webhook secret by adding a webhook under Razorpay Dashboard → Settings
   → Webhooks pointing at `https://<your-tunnel>/api/webhooks/razorpay/`
   (use `ngrok`/`cloudflared` or similar for local HTTPS reachability),
   subscribe to `payment.captured` and `payment.failed`, and put that
   secret in `RAZORPAY_WEBHOOK_SECRET`.
2. `python manage.py migrate && python manage.py runserver`
3. Open `http://127.0.0.1:8000/plans/` in a browser. Confirm the Premium
   card shows a real ₹ price (not blank, not "Coming soon").
4. Sign up / log in a fresh test user. Confirm `/plans/` now shows
   "Upgrade to Premium" (not "Log in to upgrade").
5. Click **Upgrade to Premium**. Confirm the button immediately disables
   and shows "Preparing secure checkout…", then "Opening secure payment
   window…".
6. Open browser DevTools → Network. Confirm exactly one
   `POST /api/billing/create-order/` fired, and its response contains
   `key_id`, `order_id`, `amount`, `currency` — no `RAZORPAY_KEY_SECRET`
   anywhere in the response.
7. Confirm the Razorpay Checkout modal opens (official Razorpay UI, not a
   custom one).
8. Complete a Razorpay **TEST card** payment (Razorpay's docs list a test
   card number/CVV/expiry — any future date + any CVV works in TEST
   mode).
9. Confirm the checkout modal closes and the page shows "Verifying your
   payment…" then "Payment received. Confirming your Premium
   activation…".
10. Confirm `POST /api/billing/verify-payment/` fired once, returned 200.
11. Confirm your webhook endpoint received a `payment.captured` POST
    (check your tunnel's logs, or Razorpay Dashboard → Webhooks → recent
    deliveries).
12. Confirm repeated `GET /api/billing/payment-status/<order_id>/` polls
    fire every ~2.5s and the last one returns `{"status": "CAPTURED"}`.
13. Confirm the page shows "Premium activated successfully 🎉" and
    redirects to `/profile/`.
14. On `/profile/`, confirm: the ⭐ PREMIUM pill is shown, Status is
    Active, current period dates are populated, and a "Cancel at Period
    End" button is present.
15. Confirm the credits bar/numbers on `/profile/` reflect your account's
    real balance (compare against `GET /profile/data/` in DevTools).
16. Generate something via the AI feature. Confirm credits/usage on
    `/profile/` change after a refresh.
17. Click **Cancel at Period End**, confirm the browser `confirm()`
    dialog appears, accept it. Confirm the banner shows "Cancellation
    scheduled. Premium remains active until <date>." and the button
    changes to "Keep Premium".
18. Refresh the page. Confirm the cancelled-but-still-active state
    persists correctly (this is the state most likely to regress on a
    naive implementation — verify it explicitly).
19. Click **Keep Premium**, confirm it reverts to "Cancel at Period End"
    and the banner confirms auto-renew is back on.
20. **Failure case**: start a new Upgrade, use Razorpay's documented
    TEST failure card. Confirm the page shows "Payment failed. Your
    Premium access was not activated." and a "Try Again" button, and
    that `/profile/` still shows FREE (no accidental activation).
21. **Cancel case**: start a new Upgrade, close the Razorpay modal
    without paying (click the X). Confirm "Payment cancelled. No
    Premium access was activated." and "Try Again".
22. **Double-click guard**: click Upgrade, then immediately click it
    again before the modal opens. Confirm only one
    `create-order` request fired in DevTools Network.
23. **Two tabs**: open `/plans/` in two tabs as the same FREE user, click
    Upgrade in both quickly. Confirm the second either reuses the first
    order or gets a clear "A payment is already being processed"
    message — never two separate successful charges.
24. Check the browser console throughout the whole flow — should show no
    uncaught errors, no 404s for `/static/css/billing.css`,
    `/static/js/billing.js`, or `/static/js/profile-billing.js`.
25. Repeat steps 3–19 on a narrow (mobile-width) viewport — confirm no
    horizontal scrolling and the Razorpay modal remains usable.

## PHASE 4B closure addendum — AI credit-exhaustion flow

The steps above cover purchasing from `/plans/`. These additional steps
cover the AI-generation page's own exhaustion path added in the closure
pass (`templates/main.html`).

26. As a fresh FREE test user with credits drained (or after enough real
    generations to exhaust the free grant), submit a generation on any
    tab (Code/Story/Application/Proposal). Confirm the output panel shows
    a "Credits exhausted" card with the server's real message — not a
    generic "Something went wrong", not blank, not a JS console error.
27. Confirm the card's button says "View Premium" and is a real button
    (inspect the DOM — it should NOT be `<a href="#">` or missing an
    event listener).
28. Click it. Confirm it disables immediately and shows "Preparing
    secure checkout…", same as the pricing-page flow.
29. Complete a Razorpay TEST payment from this card (same TEST flow as
    steps 8–13 above).
30. Confirm the card updates to "Premium activated 🎉" without a full
    page reload.
31. Click the tab's normal Generate button again (no manual refresh).
    Confirm it now succeeds — this proves entitlement re-derivation
    worked, not just a UI label change.
32. Repeat step 26 as a **guest** (not logged in, browsing anonymously)
    if guest AI access is enabled in your environment. Confirm the CTA
    reads "Sign in to upgrade" and links to `/login/?next=...`, not a
    purchase button (guests can't purchase — `create-order` requires
    login).
33. On the Code tab specifically, click Download/Copy/Reset after a
    successful generation. **Known pre-existing issue, unrelated to
    billing**: these currently throw a `ReferenceError` in the browser
    console because they're scoped inside `generateCode()` but invoked
    via global `onclick=` attributes — see
    `PHASE_4B_AI_BILLING_INTEGRATION.md`'s "What main.html was NOT
    changed to do". Confirm this is what you see (so it's tracked, not
    mistaken for a regression from this pass) — it was not fixed here as
    it's outside the billing/credit scope of this closure.

## Result to record here

After running the above, replace this section with the actual outcome
(pass/fail per step, screenshots/network logs if anything fails) — this
is the artifact that turns "PHASE 4B IMPLEMENTED" into "PHASE 4B
COMPLETE".
