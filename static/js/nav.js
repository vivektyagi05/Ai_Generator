/* static/js/nav.js
   PHASE 2 STEP 1 — FOUNDATION — Shared nav behavior for
   templates/partials/nav_public.html and nav_app.html.
   Mobile menu toggle only -- which link is "active" is set server-side
   via aria-current="page" in the partial (the server already knows the
   current route; duplicating that in JS would be another source of
   drift, see PHASE_2_FRONTEND_GAP_MATRIX.md GAP-02).
*/
(function () {
  "use strict";

  function init() {
    const toggle = document.querySelector("[data-ds-nav-toggle]");
    const panel = document.querySelector("[data-ds-nav-panel]");
    if (!toggle || !panel) return;

    toggle.addEventListener("click", () => {
      const isOpen = panel.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });

    panel.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        panel.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
