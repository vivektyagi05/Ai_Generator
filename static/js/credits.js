/* static/js/credits.js
   PHASE 2 STEP 1 — FOUNDATION — Shared credit indicator.

   Calls the existing GET /credits/balance/ (accounts.views.credit_balance,
   unchanged) and renders it into any element with [data-ds-credit-pill].
   Addresses PHASE_2_FRONTEND_GAP_MATRIX.md GAP-04: today balance is only
   ever shown on /profile/ (#billingCreditsFraction in templates/profile.html),
   so a user spending credits on /home/ or a generator page has no
   visibility into what they have left until they navigate away.

   This widget is display-only. It never decides whether a request is
   allowed -- that stays server-side in accounts.services.entitlement_service
   / credit_service, per this project's existing "server remains
   authoritative" rule (see AI_GENERATORS/api_views.py module docstring).

   Not wired into any template yet -- foundation only, see
   PHASE_2_STEP_1_COMPLETION_AUDIT.md.

   Usage: <span data-ds-credit-pill></span> anywhere in the app shell,
   then DS.credits.mount() once on page load.
*/
(function (global) {
  "use strict";

  const LOW_BALANCE_THRESHOLD = 5;

  function render(el, snapshot) {
    if (snapshot.available_balance === null || snapshot.available_balance === undefined) {
      el.textContent = "Unlimited";
      el.dataset.state = "unlimited";
      return;
    }
    const n = snapshot.available_balance;
    el.textContent = n + (n === 1 ? " credit" : " credits");
    if (n <= 0) {
      el.dataset.state = "empty";
    } else if (n <= LOW_BALANCE_THRESHOLD) {
      el.dataset.state = "low";
    } else {
      el.dataset.state = "ok";
    }
  }

  async function mount() {
    const targets = document.querySelectorAll("[data-ds-credit-pill]");
    if (!targets.length) return;
    if (!global.DS || !global.DS.api) {
      console.error("DS.credits requires static/js/api.js to be loaded first.");
      return;
    }
    try {
      const snapshot = await global.DS.api.get("/credits/balance/");
      targets.forEach((el) => render(el, snapshot));
    } catch (err) {
      // Non-fatal -- the pill just doesn't render. Never block the page
      // or the generator on this.
      targets.forEach((el) => {
        el.textContent = "";
        el.dataset.state = "";
      });
    }
  }

  global.DS = global.DS || {};
  global.DS.credits = { mount };
})(window);
