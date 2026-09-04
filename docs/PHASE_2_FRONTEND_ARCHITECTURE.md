# PHASE 2 — Frontend Architecture

## Current state (as audited)
8 self-contained Django templates, no `{% extends %}` anywhere, 8
independent `<style>` blocks, 6+ independent CSRF-token readers, no shared
component library. See `PHASE_2_STEP_1_FRONTEND_FORENSIC_AUDIT.md` for the
full trace and `PHASE_2_FRONTEND_GAP_MATRIX.md` for the endpoint-by-endpoint
contract.

## Target architecture

### Two shared shells (Step 5)
- **Public shell** (`templates/base_public.html` +
  `templates/partials/nav_public.html`) — login, signup, forgot-password,
  public pricing. Centers a single card; guest nav (Generators, Pricing,
  Login, Sign Up).
- **App shell** (`templates/base_app.html` +
  `templates/partials/nav_app.html`) — generators, history, profile,
  billing. Full-width product layout; authenticated nav with Generators
  dropdown, History, Pricing, credit pill, Profile, Logout.

Both shells load the same three stylesheets
(`tokens.css` → `components.css` → `shell.css`) so there is exactly one
product identity, per Step 5's requirement.

### Design system (Step 6)
See `PHASE_2_DESIGN_SYSTEM.md`.

### Shared JS architecture (Step 7)
| Responsibility | File | Replaces (once adopted) |
|---|---|---|
| fetch + CSRF + error normalization | `static/js/api.js` | GAP-02's 6 duplicate CSRF readers |
| toast notifications | `static/js/toast.js` | GAP-06's 6 hand-rolled error/success UIs |
| nav mobile toggle | `static/js/nav.js` | n/a (new capability, mobile nav didn't exist before) |
| credit indicator | `static/js/credits.js` | GAP-04 (nothing existed to replace) |

`static/js/billing.js` and `static/js/profile-billing.js` are **not**
touched or duplicated — Step 7 says don't duplicate billing state machine
logic, and Step 11 says use a correct existing endpoint/module rather than
rebuilding it. The billing flow architecture is preserved exactly as-is;
`api.js` doesn't attempt to wrap or replace it.

### Frontend state machine (Step 8)
Already real for AI generation (`main.html`'s `aiRequest()` +
credit-exhaustion/auth-required branches — see the gap matrix's GENERATORS
row) and for billing (`billing.js`'s create-order → verify → poll
sequence, preserved as-is per Step 8's explicit instruction). The
foundation's `api.js`/`toast.js` give future pages (history search,
generator deep-linking) a consistent place to express
`IDLE → LOADING → SUCCESS|ERROR` without re-deriving it — no new state
machine was invented where a working one already exists.

## What Step 10 actually shipped (foundation only)
1. `static/css/tokens.css` — design tokens
2. `static/css/components.css` — shared button/input/card/badge/alert/
   toast/modal/loading/skeleton/state/focus/credit-pill classes
3. `templates/base_public.html` — public shell
4. `templates/base_app.html` — app shell
5. `templates/partials/nav_app.html` — shared authenticated nav
6. `templates/partials/nav_public.html` — shared guest nav
7. `static/css/shell.css` — nav + mobile nav + shell layout
8. `static/js/api.js` — shared fetch/CSRF/error utility
9. `static/js/toast.js` — shared toast component
10. `static/js/credits.js` — shared credit indicator
11. `static/js/nav.js` — shared mobile-nav toggle

No existing page's markup, styling, or behavior was changed. Every file
above is new and currently unreferenced by any existing template — see
`PHASE_2_STEP_1_COMPLETION_AUDIT.md` for verification that this is true and
that nothing broke.

## What Step 10 explicitly did NOT do (per the brief)
Did not touch `templates/main.html`, `templates/login.html`,
`templates/signup.html`, `templates/forget.html`, `templates/verify_otp.html`,
`templates/profile.html`, `templates/history.html`, or `templates/plans.html`.
None of them extend the new base templates yet. That migration — page by
page, each one reviewable in isolation — is Phase 3 work.

## Backend preservation (Step 11)
Zero backend files were changed. Zero new API endpoints were created. Every
endpoint the new nav partials/JS call already exists and already returns
the shape they expect (`/credits/balance/`, confirmed against
`accounts/services/credit_service.get_balance_snapshot()`).
