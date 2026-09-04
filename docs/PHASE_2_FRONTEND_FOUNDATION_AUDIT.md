# PHASE 2 — Frontend Foundation Audit (this session's update)

This session did NOT redo Phase 2 from scratch — a prior session already
built and verified the shared foundation. This file records what was
re-confirmed and the one gap actually closed today. For the full original
audit, read (still accurate, not superseded):

- `PHASE_2_STEP_1_FRONTEND_FORENSIC_AUDIT.md` — original forensic pass
- `PHASE_2_FRONTEND_GAP_MATRIX.md` — endpoint↔frontend contract matrix + GAP-01..07
- `PHASE_2_FRONTEND_ARCHITECTURE.md` — target architecture
- `PHASE_2_STEP_1_COMPLETION_AUDIT.md` — original verification (415 tests, foundation
  files confirmed to exist and render, GAP-01/03/04 claims grep-verified)

## Re-confirmed this session (CODE-VERIFIED, read directly, not assumed)

- `templates/base_app.html`, `templates/partials/nav_app.html`,
  `static/css/tokens.css`/`components.css`/`shell.css`,
  `static/js/api.js`/`credits.js`/`toast.js`/`nav.js` all exist and match
  what the prior audit described: a real, syntactically coherent
  foundation, not wired into `main.html`/`history.html`/`profile.html`/
  `plans.html` yet.
- `DS.api` (`api.js`): single `request()` wrapper, CSRF from cookie with
  meta-tag fallback, normalizes both `/api/ai/` error body shapes
  (`{"error": "string"}` and `{"error": {"code","message"}}`).
- `DS.credits` (`credits.js`): calls the real `GET /credits/balance/`
  (unchanged `accounts.views.credit_balance`), renders into any
  `[data-ds-credit-pill]` element, degrades silently (empty pill) on
  network failure rather than blocking the page. Never computes a
  balance client-side.

## GAP-04 — CLOSED this session

`PHASE_2_FRONTEND_GAP_MATRIX.md` GAP-04: *"`/credits/balance/` has no
frontend caller — balance invisible outside `/profile/`"*.

Fix: `templates/main.html` now loads `api.js`/`credits.js`, renders a
`<span data-ds-credit-pill>` in its existing header (styled to match the
page's own dark-glass theme via a small `.credit-pill` rule — deliberately
NOT `tokens.css`/`components.css`, since pulling in the shared visual
language wholesale is the larger Phase 6 workspace-restyle, out of scope
for this fix), and calls the SAME `DS.credits.mount()` on
`DOMContentLoaded` and after each of the five generators' success paths
(credits are only ever debited server-side, after success — see
`AI_GENERATORS/api_views.py` — so a stale pill after a successful
generation would be misleading).

This is data exposure through the existing shared widget, not a second
credit-display implementation — `DS.credits` is used as-is.

**TEST-VERIFIED**: `accounts/tests/test_credit_pill_wiring.py`, 4/4
passing — scripts loaded, hook element present, mounted on load, and
exactly 6 call-sites (5 generators + initial mount) confirmed by counting
occurrences in the rendered page, not by inspection.

**NOT VERIFIED**: actual browser rendering (pixel-level pill placement,
whether `.credit-pill:empty { display: none }` behaves correctly before
the async fetch resolves) — no browser was launched, same limitation the
original Phase 2 completion audit already disclosed for the rest of the
foundation.

## Everything else in the original gap matrix — status unchanged

GAP-01 (no shared layer across pages), GAP-02 (duplicated CSRF/fetch),
GAP-03 (4 visual languages), GAP-05 (no deep-linking), GAP-06 (hand-rolled
loading/error/toast per page), GAP-07 (raw `href` vs `{% url %}`) are all
still open — closing them means migrating `main.html` (and the other
pages) onto the shared shell, which the original audit correctly scoped
as later work ("Phase 3" in the old numbering; "Phase 6" in the current
master prompt's numbering) rather than part of the foundation itself.
Not attempted in this session — see the remaining-work section of this
response and `PHASE_5_AI_GENERATOR_COMPLETION_AUDIT.md` below.
