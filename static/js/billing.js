/**
 * PHASE 4B — window.BillingFlow
 *
 * One centralized frontend state machine for the upgrade-to-Premium flow.
 * Talks ONLY to the existing Phase 4 backend contract:
 *
 *   POST /api/billing/create-order/
 *   POST /api/billing/verify-payment/
 *   GET  /api/billing/payment-status/<order_id>/
 *   GET  /api/subscription/
 *
 * This file never decides amount, plan price, or Premium status itself --
 * every one of those values is read back from a server response. A
 * checkout.js "success" callback is treated as an unverified UX signal
 * only (Phase 6); the state machine does not call it activation, and does
 * not report SUCCESS until the server confirms Payment=CAPTURED AND the
 * user's own subscription is ACTIVE (Phase 7).
 *
 * Used by templates/plans.html (new purchase) and templates/profile.html
 * (upgrade CTA inside the billing card, via static/js/profile-billing.js).
 */
(function (window, document) {
  "use strict";

  var STATES = {
    IDLE: "IDLE",
    PREPARING_ORDER: "PREPARING_ORDER",
    CHECKOUT_OPEN: "CHECKOUT_OPEN",
    PAYMENT_PROCESSING: "PAYMENT_PROCESSING",
    VERIFYING_PAYMENT: "VERIFYING_PAYMENT",
    WAITING_FOR_WEBHOOK: "WAITING_FOR_WEBHOOK",
    SUCCESS: "SUCCESS",
    FAILED: "FAILED",
    CANCELLED: "CANCELLED",
    TIMEOUT: "TIMEOUT",
    NETWORK_ERROR: "NETWORK_ERROR",
    CONFIG_UNAVAILABLE: "CONFIG_UNAVAILABLE",
  };

  var MESSAGES = {
    IDLE: "",
    PREPARING_ORDER: "Opening secure checkout\u2026",
    CHECKOUT_OPEN: "Opening secure checkout\u2026",
    PAYMENT_PROCESSING: "Processing your payment\u2026",
    VERIFYING_PAYMENT: "Verifying your payment\u2026",
    WAITING_FOR_WEBHOOK: "Payment received. Confirming your Premium activation\u2026",
    SUCCESS: "Premium activated successfully \uD83C\uDF89",
    FAILED: "Payment failed. Your Premium access was not activated.",
    CANCELLED: "Payment cancelled. No Premium access was activated.",
    TIMEOUT: "Payment was received, but confirmation is taking longer than expected. It will finish shortly \u2014 you can safely check back on this page.",
    NETWORK_ERROR: "We couldn't reach the server. Please check your connection and try again.",
    CONFIG_UNAVAILABLE: "Secure payments are temporarily unavailable. The payment service is not configured for this environment. Please try again later.",
  };

  // A state a retry button should be shown for. CONFIG_UNAVAILABLE is
  // deliberately absent -- it's a deterministic environment problem, not
  // a transient one, so retrying can't succeed until an operator
  // configures billing (Phase C).
  var RETRYABLE_STATES = {
    FAILED: true,
    CANCELLED: true,
    TIMEOUT: true,
    NETWORK_ERROR: true,
  };

  // Backend error codes (accounts/billing_views.py) that map to a
  // dedicated frontend state rather than the generic FAILED bucket.
  var CODE_TO_STATE = {
    BILLING_NOT_CONFIGURED: STATES.CONFIG_UNAVAILABLE,
  };

  var RAZORPAY_SRC = "https://checkout.razorpay.com/v1/checkout.js";
  var POLL_INTERVAL_MS = 2500;
  var MAX_POLL_ATTEMPTS = 24; // ~60s of polling for the webhook-driven CAPTURED transition
  var CONFIRM_INTERVAL_MS = 1000;
  var MAX_CONFIRM_ATTEMPTS = 5; // ~5s of polling /api/subscription/ after CAPTURED

  // PHASE 9 — Step 10/11: this duplicated static/js/api.js's
  // getCsrfToken() (same fallback logic, opposite priority order --
  // meta-tag-first here vs cookie-first there). Delegates to the shared
  // implementation now that it's loaded on every page (see api.js's own
  // header comment); keeps the original standalone logic as a fallback
  // only for the case api.js somehow isn't loaded yet, rather than
  // silently depending on script load order.
  function getCSRFToken() {
    if (window.DS && window.DS.api && typeof window.DS.api.getCsrfToken === "function") {
      return window.DS.api.getCsrfToken();
    }
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function safeJson(response) {
    return response
      .json()
      .catch(function () {
        return null;
      });
  }

  // Normalizes backend error responses (Phase 8/19) without ever
  // surfacing a traceback, a Razorpay internal, or a secret.
  function errorMessageForStatus(status, body) {
    if (body && typeof body.error === "string" && body.error) {
      return body.error;
    }
    switch (status) {
      case 401:
        return "Please log in to continue.";
      case 403:
        return "You don't have permission to do that.";
      case 404:
        return "We couldn't find that payment.";
      case 409:
        return "A payment is already being processed for your account.";
      case 422:
        return "That request couldn't be processed. Please try again.";
      case 429:
        return "Too many attempts. Please wait a moment and try again.";
      case 503:
        return "Payments are temporarily unavailable. Please try again later.";
      default:
        if (status >= 500) return "The server ran into a problem. Please try again.";
        return "Something went wrong. Please try again.";
    }
  }

  var razorpayScriptPromise = null;
  function loadRazorpayScript() {
    if (window.Razorpay) return Promise.resolve();
    if (razorpayScriptPromise) return razorpayScriptPromise;
    razorpayScriptPromise = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = RAZORPAY_SRC;
      script.async = true;
      script.onload = function () {
        resolve();
      };
      script.onerror = function () {
        razorpayScriptPromise = null;
        reject(new Error("Could not load the secure payment window."));
      };
      document.head.appendChild(script);
    });
    return razorpayScriptPromise;
  }

  /**
   * @param {Object} opts
   * @param {(state:string, message:string, extra:Object) => void} opts.onStateChange
   * @param {(payment:Object) => void} [opts.onSuccess]
   */
  function BillingFlow(opts) {
    opts = opts || {};
    this.onStateChange = typeof opts.onStateChange === "function" ? opts.onStateChange : function () {};
    this.onSuccess = typeof opts.onSuccess === "function" ? opts.onSuccess : function () {};
    this.state = STATES.IDLE;
    this.inFlight = false;
    this._pollTimer = null;
    this._pollAttempts = 0;
    this._confirmAttempts = 0;
  }

  BillingFlow.prototype._setState = function (state, extra) {
    this.state = state;
    this.onStateChange(state, MESSAGES[state], extra || {});
  };

  /**
   * Entry point. Guards against double-click/double-Enter/two-tab firing
   * by refusing to start a second checkout while one is already in flight
   * (Phase 4/7) -- the backend's uq_payment_one_created_per_user is the
   * real authority; this is just so the UI never fires two requests.
   */
  BillingFlow.prototype.upgrade = function (params) {
    params = params || {};
    if (this.inFlight) return Promise.resolve();
    this.inFlight = true;
    this._pollAttempts = 0;
    this._confirmAttempts = 0;

    var plan = params.plan || "PREMIUM";
    var billingInterval = params.billingInterval || "MONTHLY";
    var self = this;

    this._setState(STATES.PREPARING_ORDER);

    return fetch("/api/billing/create-order/", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCSRFToken(),
      },
      body: JSON.stringify({ plan: plan, billing_interval: billingInterval }),
    })
      .then(function (resp) {
        return safeJson(resp).then(function (body) {
          if (!resp.ok) {
            var state = (body && CODE_TO_STATE[body.code]) || STATES.FAILED;
            self._setState(state, { message: errorMessageForStatus(resp.status, body), code: body && body.code });
            self.inFlight = false;
            return null;
          }
          return body;
        });
      })
      .then(function (checkoutInfo) {
        if (!checkoutInfo) return;
        return self._openCheckout(checkoutInfo);
      })
      .catch(function () {
        self._setState(STATES.NETWORK_ERROR);
        self.inFlight = false;
      });
  };

  BillingFlow.prototype._openCheckout = function (checkoutInfo) {
    var self = this;
    this._setState(STATES.CHECKOUT_OPEN);

    return loadRazorpayScript()
      .catch(function () {
        self._setState(STATES.NETWORK_ERROR, { message: "Could not load the secure payment window." });
        self.inFlight = false;
        return Promise.reject(new Error("razorpay-script-failed"));
      })
      .then(function () {
        var options = {
          key: checkoutInfo.key_id,
          amount: checkoutInfo.amount,
          currency: checkoutInfo.currency,
          order_id: checkoutInfo.order_id,
          name: "AI Generators",
          description: checkoutInfo.plan + " \u2014 " + checkoutInfo.billing_interval,
          theme: { color: "#ff7a3d" },
          handler: function (response) {
            self._verifyPayment(checkoutInfo, response);
          },
          modal: {
            ondismiss: function () {
              // Only a real cancel if we're still waiting on the modal --
              // the handler() above already advanced past CHECKOUT_OPEN
              // for a real success, so this can't misfire after payment.
              if (self.state === STATES.CHECKOUT_OPEN || self.state === STATES.PAYMENT_PROCESSING) {
                self._setState(STATES.CANCELLED);
                self.inFlight = false;
              }
            },
          },
        };

        var rzp = new window.Razorpay(options);
        rzp.on("payment.failed", function () {
          self._setState(STATES.FAILED);
          self.inFlight = false;
        });
        self._setState(STATES.PAYMENT_PROCESSING);
        rzp.open();
      })
      .catch(function () {
        // already handled above (state already set) -- swallow so this
        // doesn't surface as an unhandled rejection in the console.
      });
  };

  /**
   * Checkout.js reporting success is NOT Premium activation (Phase 6) --
   * it's only enough to (a) verify the signature server-side and (b) let
   * the UI show a trustworthy "payment received" state while we wait for
   * the webhook-driven capture.
   */
  BillingFlow.prototype._verifyPayment = function (checkoutInfo, razorpayResponse) {
    var self = this;
    this._setState(STATES.VERIFYING_PAYMENT);

    return fetch("/api/billing/verify-payment/", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCSRFToken(),
      },
      body: JSON.stringify({
        order_id: razorpayResponse.razorpay_order_id || checkoutInfo.order_id,
        payment_id: razorpayResponse.razorpay_payment_id,
        signature: razorpayResponse.razorpay_signature,
      }),
    })
      .then(function (resp) {
        return safeJson(resp).then(function (body) {
          if (!resp.ok) {
            var state = (body && CODE_TO_STATE[body.code]) || STATES.FAILED;
            self._setState(state, { message: errorMessageForStatus(resp.status, body), code: body && body.code });
            self.inFlight = false;
            return;
          }
          self._setState(STATES.WAITING_FOR_WEBHOOK);
          self._pollStatus(checkoutInfo.order_id, checkoutInfo.plan);
        });
      })
      .catch(function () {
        self._setState(STATES.NETWORK_ERROR);
        self.inFlight = false;
      });
  };

  /** Bounded polling of the webhook-driven CAPTURED transition (Phase 6). */
  BillingFlow.prototype._pollStatus = function (orderId, planCode) {
    var self = this;
    clearTimeout(this._pollTimer);
    this._pollTimer = setTimeout(function () {
      self._pollAttempts += 1;
      fetch("/api/billing/payment-status/" + encodeURIComponent(orderId) + "/", {
        credentials: "same-origin",
      })
        .then(function (resp) {
          return safeJson(resp).then(function (body) {
            if (!resp.ok) {
              return self._continuePollOrTimeout(orderId, planCode);
            }
            if (body && body.status === "CAPTURED") {
              return self._confirmActivation(orderId, planCode, body);
            }
            if (body && body.status === "FAILED") {
              self._setState(STATES.FAILED);
              self.inFlight = false;
              return;
            }
            return self._continuePollOrTimeout(orderId, planCode);
          });
        })
        .catch(function () {
          return self._continuePollOrTimeout(orderId, planCode);
        });
    }, POLL_INTERVAL_MS);
  };

  BillingFlow.prototype._continuePollOrTimeout = function (orderId, planCode) {
    if (this._pollAttempts >= MAX_POLL_ATTEMPTS) {
      this._setState(STATES.TIMEOUT);
      this.inFlight = false;
      return;
    }
    this._pollStatus(orderId, planCode);
  };

  /**
   * Phase 7: Payment=CAPTURED alone is not sufficient -- confirm the
   * caller's own Subscription has actually reached ACTIVE (payment_service
   * activates it in the same DB transaction as the CAPTURED write, so this
   * should resolve on the first check; the short bounded retry only covers
   * the narrow window where this request lands between those two reads).
   */
  BillingFlow.prototype._confirmActivation = function (orderId, planCode, paymentBody) {
    var self = this;
    this._confirmAttempts += 1;
    fetch("/api/subscription/", { credentials: "same-origin" })
      .then(function (resp) {
        return safeJson(resp).then(function (subBody) {
          var active = subBody && subBody.status === "ACTIVE" && (!planCode || subBody.plan === planCode);
          if (active) {
            self._setState(STATES.SUCCESS);
            self.inFlight = false;
            self.onSuccess(paymentBody);
            return;
          }
          if (self._confirmAttempts >= MAX_CONFIRM_ATTEMPTS) {
            // Payment is genuinely captured -- money was taken -- but this
            // client hasn't observed subscription activation yet. Report
            // SUCCESS (Payment=CAPTURED is the financially authoritative
            // fact) while noting activation is still catching up, rather
            // than leaving the user on an indefinite spinner.
            self._setState(STATES.SUCCESS, { activationPending: true });
            self.inFlight = false;
            self.onSuccess(paymentBody);
            return;
          }
          setTimeout(function () {
            self._confirmActivation(orderId, planCode, paymentBody);
          }, CONFIRM_INTERVAL_MS);
        });
      })
      .catch(function () {
        if (self._confirmAttempts >= MAX_CONFIRM_ATTEMPTS) {
          self._setState(STATES.SUCCESS, { activationPending: true });
          self.inFlight = false;
          self.onSuccess(paymentBody);
          return;
        }
        setTimeout(function () {
          self._confirmActivation(orderId, planCode, paymentBody);
        }, CONFIRM_INTERVAL_MS);
      });
  };

  BillingFlow.prototype.reset = function () {
    clearTimeout(this._pollTimer);
    this._pollAttempts = 0;
    this._confirmAttempts = 0;
    this.inFlight = false;
    this._setState(STATES.IDLE);
  };

  window.BillingFlow = {
    STATES: STATES,
    MESSAGES: MESSAGES,
    RETRYABLE_STATES: RETRYABLE_STATES,
    create: function (opts) {
      return new BillingFlow(opts);
    },
    getCSRFToken: getCSRFToken,
    errorMessageForStatus: errorMessageForStatus,
  };
})(window, document);
