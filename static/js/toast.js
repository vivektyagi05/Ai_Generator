/* static/js/toast.js
   PHASE 2 STEP 1 — FOUNDATION — Shared toast notifications.
   Pairs with .ds-toast-* rules in static/css/components.css.
   Not wired into any existing page yet -- see PHASE_2_STEP_1_COMPLETION_AUDIT.md.

   Usage:
     DS.toast.show("Profile updated", { type: "success" });
     DS.toast.show("Could not save changes", { type: "danger", duration: 6000 });
*/
(function (global) {
  "use strict";

  let region = null;

  function ensureRegion() {
    if (region && document.body.contains(region)) return region;
    region = document.createElement("div");
    region.className = "ds-toast-region";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.appendChild(region);
    return region;
  }

  function show(message, { type = "info", duration = 4000 } = {}) {
    const el = document.createElement("div");
    el.className = "ds-toast ds-toast-" + type;
    el.setAttribute("role", type === "danger" ? "alert" : "status");

    const text = document.createElement("span");
    text.textContent = message;
    el.appendChild(text);

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "ds-toast-close";
    closeBtn.setAttribute("aria-label", "Dismiss");
    closeBtn.textContent = "\u00d7";
    closeBtn.addEventListener("click", () => el.remove());
    el.appendChild(closeBtn);

    ensureRegion().appendChild(el);

    if (duration > 0) {
      setTimeout(() => el.remove(), duration);
    }
    return el;
  }

  global.DS = global.DS || {};
  global.DS.toast = {
    show,
    success: (msg, opts) => show(msg, Object.assign({ type: "success" }, opts)),
    danger: (msg, opts) => show(msg, Object.assign({ type: "danger" }, opts)),
    warning: (msg, opts) => show(msg, Object.assign({ type: "warning" }, opts)),
    info: (msg, opts) => show(msg, Object.assign({ type: "info" }, opts)),
  };
})(window);
