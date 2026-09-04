# PHASE 2 — Product Information Architecture

## Guest navigation
```
Home (Generators) · Pricing · Login · Sign Up
```
Maps to existing routes: `home`, `plans_page`, `login`, `signup` — no new
backend routes required.

## Authenticated navigation
```
Generators (dropdown: Photo · Story · Application · Proposal · Code) ·
History · Pricing · [credit pill] · Profile · Logout
```
"Credits" is not a separate nav item — it's the persistent pill next to
Profile (see GAP-04), always visible rather than one more click away.
Matches existing routes: `home`, `history`, `plans_page`, `profile`,
`logout`.

## Generators menu
Photo · Story · Application · Proposal · Code — all five link to `home`
today, since `main.html` has no per-generator URL or hash route yet
(GAP-05). This is intentionally not invented in Step 1: adding real
deep-linking means changing `main.html`'s `switchTab()` to read
`location.hash` on load, which is page-content work reserved for Phase 3,
not foundation. The dropdown exists now so it's visible and correct
(it does reach the generators) even before deep-linking ships.

## Mobile navigation
Collapses `.ds-nav-links` behind a hamburger toggle
(`static/css/shell.css`'s `@media (max-width: 768px)` rule); full-screen
panel lists the same links flat (no dropdown-in-dropdown on mobile — the
Generators dropdown becomes a single "Generators" link, matching how the
desktop dropdown itself points at one URL today anyway). Implemented in
`templates/partials/nav_app.html` / `nav_public.html` + `static/js/nav.js`.

## Premium discoverability
Two touchpoints, neither intrusive:
1. Persistent "Pricing" link in main nav on every page (guest and
   authenticated) — was previously only reachable if a page happened to
   link to `/plans/` (`main.html` did, via the credit-exhaustion modal;
   `profile.html` did; `login.html`/`signup.html`/`history.html` did not).
2. The credit pill turns visibly amber/red at low/zero balance
   (`.ds-credit-pill[data-state="low"|"empty"]` in `components.css`),
   which is a natural, non-intrusive nudge toward Pricing at the moment
   it's actually relevant — not a banner shown unconditionally.

No "Premium" badge/upsell was invented beyond what the backend actually
supports: per `accounts/entitlement_config.py`, Premium plan activation
isn't live yet ("Activation is not available yet — see the public plan
page"), so the foundation does not add a fake "Upgrade" CTA anywhere —
that would be exactly the kind of fabricated functionality Step 9
prohibits.
