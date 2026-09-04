/* static/js/api.js
   PHASE 2 STEP 1 — FOUNDATION — Shared API/CSRF/error utility.

   Consolidates logic that today is copy-pasted with small variations in:
     - templates/main.html      (getCsrfToken(), aiRequest())
     - templates/profile.html   (getCSRFToken(), 4 call sites)
     - templates/forget.html    (getCSRF(), 4 call sites)
     - templates/verify_otp.html (inline document.querySelector CSRF read)
     - static/js/billing.js     (getCSRFToken())
     - static/js/profile-billing.js (csrfToken())
   See PHASE_2_FRONTEND_GAP_MATRIX.md GAP-02 ("6 independent CSRF-token
   readers") and PHASE_2_FRONTEND_ARCHITECTURE.md "Step 7".

   Not wired into any existing page yet (Step 10 note: foundation only).
   Existing pages keep their own working copies until Phase 3 migrates
   each page individually -- see PHASE_2_FRONTEND_GAP_MATRIX.md GAP-01.

   Usage:
     const data = await DS.api.postJson("/api/ai/", { prompt, feature });
     // throws DS.api.ApiError on non-2xx, with .status and .payload

   PHASE 9 — Step 17 doc-accuracy fix: the note below ("Not wired into
   any existing page yet") was true when this file was created in Phase 2
   but has been stale since Phase 3+ actually adopted it -- confirmed by
   grep: templates/profile.html, templates/main.html, templates/forget.html,
   templates/verify_otp.html, and static/js/credits.js all call DS.api
   and DS.util functions today. Left the original note below for history
   rather than deleting it -- just noting it's outdated.
*/
(function (global) {
  "use strict";

  function readCsrfCookie() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  // Falls back to a <meta name="csrf-token"> tag if the cookie isn't
  // readable (matches the pattern already used by templates/plans.html
  // and templates/verify_otp.html).
  function getCsrfToken() {
    const fromCookie = readCsrfCookie();
    if (fromCookie) return fromCookie;
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  /* Normalizes the two error body shapes /api/ai/ and friends return
     today (see AI_GENERATORS/api_views.py module docstring: plain
     {"error": "..."} vs structured {"error": {"code","message"}}). */
  function extractErrorMessage(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload.error === "string") return payload.error;
    if (payload.error && typeof payload.error.message === "string") return payload.error.message;
    if (typeof payload.detail === "string") return payload.detail;
    return fallback;
  }

  async function request(url, { method = "GET", body, headers = {}, credentials = "same-origin" } = {}) {
    const finalHeaders = Object.assign({}, headers);
    const isUnsafe = method !== "GET" && method !== "HEAD";
    if (isUnsafe) {
      finalHeaders["X-CSRFToken"] = getCsrfToken();
      if (body !== undefined && !finalHeaders["Content-Type"]) {
        finalHeaders["Content-Type"] = "application/json";
      }
    }

    let response;
    try {
      response = await fetch(url, {
        method,
        credentials,
        headers: finalHeaders,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (networkErr) {
      throw new ApiError("Network error -- please check your connection.", 0, null);
    }

    let payload = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (parseErr) {
        payload = null;
      }
    }

    if (!response.ok) {
      throw new ApiError(
        extractErrorMessage(payload, "Something went wrong. Please try again."),
        response.status,
        payload
      );
    }

    return payload;
  }

  const api = {
    ApiError,
    getCsrfToken,
    get: (url, opts) => request(url, Object.assign({ method: "GET" }, opts)),
    postJson: (url, body, opts) => request(url, Object.assign({ method: "POST", body }, opts)),
  };

  // PHASE 7 GOLDEN UI / security sweep: escapeHtml was previously
  // declared locally inside templates/main.html's own <script> block
  // only, so every OTHER page building HTML strings for innerHTML from
  // server data (profile.html's activity feed, built from the user's
  // own ChatHistory.query text -- free-form user input) had no shared
  // way to escape it and simply didn't, which is a real (self-)XSS gap:
  // a user's own stored prompt could execute script when rendered back
  // into their own profile page. api.js already loads on every page via
  // nav_app.html, before each page's own <script> block runs, so this is
  // one shared implementation instead of a second copy-pasted one.
  function escapeHtml(text) {
    if (!text) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const util = { escapeHtml };

  global.DS = global.DS || {};
  global.DS.api = api;
  global.DS.util = util;
})(window);
