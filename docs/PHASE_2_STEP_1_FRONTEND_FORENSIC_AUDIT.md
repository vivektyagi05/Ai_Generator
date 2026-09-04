# PHASE 2 STEP 1 — Frontend Forensic Audit

Scope: every file in `templates/`, `static/css/`, `static/js/`, plus how each
page talks to `accounts/urls.py` and `AI_GENERATORS/urls.py`. Traced actual
code, not the prior phase's audits (which covered Photo Generator's backend
integration only — see `PHASE_2_FRONTEND_ARCHITECTURE.md` for how this audit
relates to that work).

## 1. Inventory

| File | Lines | Role |
|---|---|---|
| `templates/main.html` | 2417 | Home / all 5 generators (tab-switched SPA-in-a-page) |
| `templates/login.html` | 811 | Login |
| `templates/signup.html` | 1112 | Signup + OTP hand-off |
| `templates/verify_otp.html` | 692 | Signup OTP verification |
| `templates/forget.html` | 1119 | Forgot-password flow (send/verify/reset) |
| `templates/profile.html` | 1284 | Profile, billing status, account actions |
| `templates/history.html` | 309 | Generation history |
| `templates/plans.html` | 209 | Pricing / plan catalog |
| `static/js/billing.js` | 394 | Shared billing flow (order → verify → poll) |
| `static/js/profile-billing.js` | 204 | Profile page's billing summary widget |
| `static/css/billing.css` | 329 | Billing UI, composes with `var(--x, fallback)` |

Every template is a fully self-contained HTML document — none uses
`{% extends %}`. Each has its own `<style>` block (8 independent stylesheets)
and its own inline `<script>` block. There is no shared base template.

## 2. What's already correct (do not rebuild)

- **All 5 generators already call the unified endpoint.** `main.html`'s
  `aiRequest()` (single fetch/CSRF/idempotency helper, ~line 1476) is called
  by Code (line 1657), Photo (line 1817), Story (line 1972), Application
  (line 2157), and Proposal (line 2317), each passing its own `feature`
  string. This was true before this audit for 4 of the 5 — Photo is the one
  Phase 1 migrated off a direct client-side provider call. The claim in the
  brief ("Photo now uses POST /api/ai/... browser no longer directly
  initiates Photo generation") checks out in the code.
- **CSRF is never disabled.** Every unsafe request across every template
  sends `X-CSRFToken` read from the `csrftoken` cookie (or a `<meta
  name="csrf-token">` fallback in `plans.html`/`verify_otp.html`). No
  `@csrf_exempt` view is called from a template.
- **No direct third-party provider calls remain in any template.** Grepped
  every `fetch(` call in every template; none targets an external domain.
- **Billing flow is centralized.** `static/js/billing.js` is the one place
  that knows the create-order → Razorpay checkout → verify-payment sequence;
  `profile.html` and `plans.html` both load it rather than reimplementing it.
- **No fake/decorative UI found.** Searched for "Coming soon", dead links,
  disabled buttons with no handler, hardcoded credit balances, and fake
  premium state. All `disabled` attributes found are real loading-state
  toggles (`btn.disabled = true` during an in-flight request) or genuinely
  server-driven states (`plans.html`'s "Your current plan" button, `disabled`
  because that plan really is active). One button in `main.html` (~line
  1387) explicitly avoids being fake: the comment reads "link instead of a
  fake purchase button," and the code links to `/plans/` rather than faking
  a purchase.

## 3. Real gaps found (traced, not assumed)

### GAP-01 — No shared template/CSS/JS layer (P1)
Eight templates, eight independent `<style>` blocks, eight independent
inline `<script>` blocks. Confirmed by diff: `login.html`, `signup.html`,
`forget.html`, and `profile.html` each define an **identical, byte-for-byte**
`:root` token block (`--bg-main: #f7f7f8`, `--primary-color: #10a37f`, etc.)
independently. That's the same 13-line block copy-pasted 4 times. Any
future rebrand requires editing 4+ files and hoping they stay in sync.

### GAP-02 — CSRF/fetch/error logic duplicated 6+ times (P2)
Six independent implementations of "read the CSRF cookie" exist:
`main.html` (`getCsrfToken`), `profile.html` (`getCSRFToken`), `forget.html`
(`getCSRF`), `verify_otp.html` (inline `document.querySelector`),
`billing.js` (`getCSRFToken`), `profile-billing.js` (`csrfToken`). Same
logic, four different function names, copy-pasted regex. Not a correctness
bug today (each copy works), but a maintenance/consistency risk, and every
new page pays the copy-paste tax again.

### GAP-03 — Four incompatible visual languages, not one (P1)
Confirmed by direct inspection of each page's palette/typography:
1. **Neutral/utility** (`#f7f7f8` bg, `#10a37f` green primary, system font
   stack) — `login.html`, `signup.html`, `forget.html`, `profile.html`,
   `plans.html` (5 of 8 pages; `plans.html` inlines the same green/gray
   though it doesn't redeclare the full `:root`).
2. **Dark hero + pink accent** (`main.html`) — full-bleed background photo,
   `#ff6b9d` accent, `'Segoe UI'` — visually unrelated to every other page
   a signed-in user sees next.
3. **Purple gradient** (`history.html`) — `linear-gradient(135deg,#667eea,
   #764ba2)`, no relation to either of the above.
4. **Editorial serif** (`verify_otp.html`) — Google Fonts `DM Sans` +
   `Crimson Pro`, `#2C3E50`/`#E67E22` palette, loaded from
   `fonts.googleapis.com` — the only page pulling web fonts at all.

A user's first five minutes (signup → verify OTP → land on main.html →
check history) crosses all four of these. The neutral palette (#1) is
already the majority pattern and is what `static/css/billing.css` composes
with (`var(--primary-color, #10a37f)`) — it's the de-facto standard already,
just not centralized. See `PHASE_2_DESIGN_SYSTEM.md`.

### GAP-04 — Credit balance is not visible outside `/profile/` (P1)
`grep`'d every template for credit-balance display: the only place a number
appears is `templates/profile.html` line 887
(`#billingCreditsFraction`). A user actively spending credits on `/home/`
(the generator page) has zero visibility into what they have left — they
find out only when a request 402s ("Credits exhausted"). The backend
already exposes `GET /credits/balance/` (`accounts/views.py:705`,
`credit_service.get_balance_snapshot()`) — this is a frontend exposure gap,
not a missing capability.

### GAP-05 — No cross-page or deep-link path to a specific generator (P2)
`main.html` is the only URL that reaches any generator, and generator
selection is 100% client-side (`switchTab('photo', this)` toggles a CSS
class — no `location.hash`, no query param). There is no way to link
someone directly to "the Proposal Maker" from anywhere — email, docs,
another page's nav — everyone lands on whichever tab is marked `active` by
default (Photo) and clicks again.

### GAP-06 — Every page reimplements loading/error/toast UI from scratch (P2)
No shared toast, modal, or empty-state component exists. `forget.html`,
`login.html`, `signup.html`, `verify_otp.html`, `profile.html`, and
`main.html` each hand-roll their own inline error-message `<div>` with
page-specific classes and independent show/hide logic. Functionally
adequate per-page; architecturally, six independent implementations of the
same three UI states.

### GAP-07 — Guest navigation is inconsistent across pages (P3)
`login.html` links to `/home/`, `/signup/`, `/forgot/` — all raw hrefs, no
`{% url %}`. `signup.html` links to `/login/` as a raw href too, but uses
`{% url 'signup' %}` for its own form action. `plans.html` and
`verify_otp.html` use `{% url %}` consistently. Inconsistent
templating convention (not a broken link — checked, every raw href does
resolve to a real route) but a latent risk: a raw href silently goes stale
if a URL is ever renamed, where `{% url %}` would fail loudly at render time.

## 4. Nothing here was reported as "missing" without cause
Per the audit rule: a capability was only flagged if (a) the backend exposes
it and the frontend doesn't surface it (GAP-04), or (b) a flow genuinely
cannot complete or cannot be reached (GAP-05, partially — the flow completes,
it just can't be linked to directly). GAP-01/02/03/06/07 are architecture/
consistency findings, not missing-feature findings, and are classified P1–P3
accordingly rather than P0.

No P0 (product-correctness/security) issues were found in the frontend.
