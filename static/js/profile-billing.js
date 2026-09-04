/**
 * PHASE 4B — profile billing section.
 *
 * Consumes the payload already returned by GET /profile/data/ (which now
 * carries entitlement + subscription + credits, added alongside this
 * frontend work by extending that one existing view -- see accounts/
 * views.py:profile_data). Talks to /api/subscription/cancel/ and
 * /api/subscription/restore/ for management actions, and reuses
 * window.BillingFlow (static/js/billing.js) for the upgrade purchase flow.
 * No new billing/credit system is introduced here.
 */
(function (window, document) {
  "use strict";

  function csrfToken() {
    return window.BillingFlow ? window.BillingFlow.getCSRFToken() : "";
  }

  function fmtDate(iso) {
    if (!iso) return "-";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "-";
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  }

  /**
   * Renders the billing card from a /profile/data/ payload. Every number
   * and label comes from `data` -- nothing here is a hardcoded price,
   * credit count, or plan name (Step 27).
   */
  function renderBillingSection(data) {
    var entitlement = data.entitlement || {};
    var subscription = data.subscription || {};
    var credits = data.credits || {};
    var plan = entitlement.plan || subscription.plan || "FREE";
    var isPremium = plan !== "FREE" && plan !== "GUEST";

    document.getElementById("premiumPill").style.display = isPremium ? "inline-flex" : "none";
    document.getElementById("billingPlanName").textContent = isPremium ? plan.charAt(0) + plan.slice(1).toLowerCase() : "Free";

    var statusRow = document.getElementById("billingStatusRow");
    var periodRow = document.getElementById("billingPeriodRow");
    var renewRow = document.getElementById("billingRenewRow");

    if (isPremium && subscription.status) {
      statusRow.style.display = "flex";
      document.getElementById("billingSubStatus").textContent =
        subscription.status.charAt(0) + subscription.status.slice(1).toLowerCase().replace("_", " ");
    } else {
      statusRow.style.display = "none";
    }

    if (isPremium && subscription.current_period_start && subscription.current_period_end) {
      periodRow.style.display = "flex";
      document.getElementById("billingPeriod").textContent =
        fmtDate(subscription.current_period_start) + " \u2192 " + fmtDate(subscription.current_period_end);
    } else {
      periodRow.style.display = "none";
    }

    if (isPremium && subscription.current_period_end) {
      renewRow.style.display = "flex";
      document.getElementById("billingRenewLabel").textContent = subscription.cancel_at_period_end ? "Premium ends" : "Renews";
      document.getElementById("billingRenewDate").textContent = fmtDate(subscription.current_period_end);
    } else {
      renewRow.style.display = "none";
    }

    // ---- credits / usage ----
    var balance = typeof credits.balance === "number" ? credits.balance : 0;
    var lifetimeEarned = typeof credits.lifetime_earned === "number" ? credits.lifetime_earned : balance;
    var lifetimeUsed = typeof credits.lifetime_used === "number" ? credits.lifetime_used : 0;
    var total = lifetimeEarned > 0 ? lifetimeEarned : balance + lifetimeUsed;
    var pct = total > 0 ? Math.min(100, Math.round((lifetimeUsed / total) * 100)) : 0;

    document.getElementById("billingCreditsFraction").textContent = balance + " remaining / " + total + " total";
    var fill = document.getElementById("billingUsageFill");
    fill.style.width = pct + "%";
    document.getElementById("billingUsageBar").classList.toggle("is-critical", pct >= 90);
    document.getElementById("billingUsagePct").textContent = pct + "% used";
    document.getElementById("billingUsageTotal").textContent = "of " + total + " total";

    // ---- action buttons ----
    var upgradeBtn = document.getElementById("billingUpgradeBtn");
    var manageBtn = document.getElementById("billingManageBtn");
    var restoreBtn = document.getElementById("billingRestoreBtn");

    upgradeBtn.style.display = isPremium ? "none" : "inline-block";
    manageBtn.style.display = isPremium && !subscription.cancel_at_period_end ? "inline-block" : "none";
    restoreBtn.style.display = isPremium && subscription.cancel_at_period_end ? "inline-block" : "none";

    // ---- lapsed subscription notice (PHASE 8B Rule 5) ----
    // Only ever shown when isPremium is false (no live subscription) --
    // a genuinely refunded/expired history is otherwise invisible, since
    // FREE-and-never-subscribed looks identical to FREE-after-refund
    // without this.
    var lapsedNotice = document.getElementById("billingLapsedNotice");
    var lapsedText = document.getElementById("billingLapsedNoticeText");
    var lapsed = data.lapsed_subscription;
    if (!isPremium && lapsed) {
      var endedDate = fmtDate(lapsed.ended_at);
      if (lapsed.reason === "refunded") {
        lapsedText.textContent =
          "Your " + (lapsed.plan.charAt(0) + lapsed.plan.slice(1).toLowerCase()) +
          " payment was refunded and access was revoked on " + endedDate +
          ". You can upgrade again any time.";
      } else {
        lapsedText.textContent =
          "Your " + (lapsed.plan.charAt(0) + lapsed.plan.slice(1).toLowerCase()) +
          " plan ended on " + endedDate + ". Upgrade again any time.";
      }
      lapsedNotice.classList.add("visible");
    } else {
      lapsedNotice.classList.remove("visible");
    }
  }

  function showActionBanner(kind, message, showRetry) {
    var banner = document.getElementById("billingActionBanner");
    var text = document.getElementById("billingActionBannerText");
    var retry = document.getElementById("billingActionRetryBtn");
    banner.classList.remove("state-success", "state-failed", "state-error");
    if (kind) banner.classList.add(kind);
    banner.classList.add("visible");
    text.textContent = message;
    retry.style.display = showRetry ? "inline-block" : "none";
  }

  function hideActionBanner() {
    document.getElementById("billingActionBanner").classList.remove("visible");
  }

  /** Wires the Upgrade / Cancel-at-period-end / Restore buttons. */
  function wireBillingActions(reloadProfile) {
    var upgradeBtn = document.getElementById("billingUpgradeBtn");
    var manageBtn = document.getElementById("billingManageBtn");
    var restoreBtn = document.getElementById("billingRestoreBtn");
    var retryBtn = document.getElementById("billingActionRetryBtn");

    var flow = window.BillingFlow.create({
      onStateChange: function (state, message, extra) {
        var STATES = window.BillingFlow.STATES;
        if (state === STATES.IDLE) {
          hideActionBanner();
          upgradeBtn.disabled = false;
          upgradeBtn.textContent = "Upgrade securely";
          return;
        }
        if (state === STATES.SUCCESS) {
          showActionBanner("state-success", (extra && extra.message) || message, false);
          upgradeBtn.disabled = true;
          reloadProfile();
          return;
        }
        if (window.BillingFlow.RETRYABLE_STATES[state]) {
          showActionBanner("state-failed", (extra && extra.message) || message, true);
          upgradeBtn.disabled = false;
          upgradeBtn.textContent = "Try Again";
          return;
        }
        showActionBanner(state === "CONFIG_UNAVAILABLE" ? "state-failed" : "", (extra && extra.message) || message, false);
        upgradeBtn.disabled = true;
        upgradeBtn.textContent = state === "CONFIG_UNAVAILABLE" ? "Secure checkout unavailable" : message;
      },
    });

    upgradeBtn.addEventListener("click", function () {
      flow.upgrade({ plan: "PREMIUM", billingInterval: "MONTHLY" });
    });
    retryBtn.addEventListener("click", function () {
      flow.reset();
      flow.upgrade({ plan: "PREMIUM", billingInterval: "MONTHLY" });
    });

    function postSubscriptionAction(url, confirmMessage, busyLabel, btn) {
      if (confirmMessage && !window.confirm(confirmMessage)) return;
      btn.disabled = true;
      var originalLabel = btn.textContent;
      btn.textContent = busyLabel;
      fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify({}),
      })
        .then(function (resp) {
          return resp.json().then(function (body) {
            if (!resp.ok) {
              throw new Error((body && body.error) || window.BillingFlow.errorMessageForStatus(resp.status, body));
            }
            return body;
          });
        })
        .then(function (body) {
          if (body.cancel_at_period_end) {
            showActionBanner("state-success", "Cancellation scheduled. Premium remains active until " + fmtDate(body.current_period_end) + ".", false);
          } else {
            showActionBanner("state-success", "Premium is active. Auto-renew is back on.", false);
          }
          reloadProfile();
        })
        .catch(function (err) {
          showActionBanner("state-failed", err.message || "Something went wrong. Please try again.", false);
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = originalLabel;
        });
    }

    manageBtn.addEventListener("click", function () {
      postSubscriptionAction(
        "/api/subscription/cancel/",
        "Cancel auto-renew? You'll keep Premium access until the end of your current billing period.",
        "Cancelling\u2026",
        manageBtn
      );
    });

    restoreBtn.addEventListener("click", function () {
      postSubscriptionAction("/api/subscription/restore/", null, "Restoring\u2026", restoreBtn);
    });
  }

  window.ProfileBilling = {
    render: renderBillingSection,
    wireActions: wireBillingActions,
  };
})(window, document);
