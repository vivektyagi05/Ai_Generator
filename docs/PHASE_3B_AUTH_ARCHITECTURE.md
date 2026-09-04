# PHASE 3B AUTH — ARCHITECTURE

## Scope

Four templates: `templates/login.html`, `templates/signup.html`,
`templates/verify_otp.html`, `templates/forget.html`. No backend file
was created or modified for this phase (Rule 10 — no defect was found
that required it). Three non-auth templates received a minimal,
same-behavior nav-link fix (see `PHASE_3B_AUTH_FORENSIC_FINDINGS.md`
§4): `main.html`, `profile.html`, `history.html`.

## Shared foundation used (all pre-existing, from "Phase 2 Step 1")

- `templates/base_public.html` — the shell every auth page now
  `{% extends %}`. Provides `<head>` (tokens/components CSS), the
  public nav (`partials/nav_public.html`), and `{% block content %}` /
  `{% block extra_head %}` / `{% block extra_scripts %}`.
- `static/css/tokens.css` — the single `:root` custom-property source
  (colors, spacing, radii, shadows). All four pages now resolve their
  visuals from here instead of an inline `:root` block.
- `static/css/components.css` — `.ds-card`, `.ds-field`/`.ds-label`/
  `.ds-input`, `.ds-btn`/`.ds-btn-primary`/`.ds-btn-secondary`/
  `.ds-btn-ghost`, `.ds-alert-*`, `.ds-spinner`, `.ds-field-error-text`/
  `.ds-field-hint`. Used identically across all four pages.
- `static/js/api.js` (`DS.api`) — `getCsrfToken()` is reused on every
  page (including the two, login/signup, that submit as real HTML
  forms with `{% csrf_token %}` and don't need the JS reader at all).
  `postJson()` is used only where the real backend endpoint is a
  no-body or JSON-compatible POST: `/resend-otp/` and
  `/forgot/resend-otp/`. It is deliberately **not** used for
  `/verify-otp/`, `/forgot/verify-otp/`, `/forgot/send-otp/`, or
  `/forgot/reset-password/`, because those views read
  `request.POST` (form-encoded), and `DS.api.postJson` sends
  `application/json` — see each page's header comment and
  `PHASE_3B_AUTH_BACKEND_CONTRACT.md` for why that distinction matters.
- `static/js/toast.js` (`DS.toast`) — loaded (via `nav_public.html`)
  but not invoked by any of the four pages; see forensic findings §3
  for why an in-form `ds-alert` was used instead.

## Per-page structure

Each page follows the same shape:

```
{% extends "base_public.html" %}
{% load static %}
{# header comment: backend contract this page is built on, cited
   file/line facts, and an itemized "removed from the previous
   version" list #}
{% block title %}...{% endblock %}
{% block extra_head %}  <!-- only verify_otp.html / forget.html: OTP-box CSS -->
{% block content %}     <!-- the .ds-card, real form(s)/fetch calls -->
{% block extra_scripts %}  <!-- vanilla JS, IIFE-wrapped, no framework -->
```

No client-side framework, build step, or new dependency was
introduced — same as every other template in the project.

## Request pattern per page (see `PHASE_3B_AUTH_BACKEND_CONTRACT.md` for
the full endpoint-by-endpoint contract)

| Page | Submission mechanism | Why |
|---|---|---|
| `login.html` | Real `<form method="post">`, full-page navigation | Backend returns a 302 or a re-rendered page — there is no JSON path to branch on |
| `signup.html` | Real `<form method="post">`, full-page navigation | Same — success is `redirect("verify_otp")` |
| `verify_otp.html` | `fetch()` + `FormData`, JS-controlled (multipart, not JSON) | Backend always returns 200 JSON; a full-page POST would lose the client-side "here's your error, try again" UX the OTP boxes need |
| `verify_otp.html` resend | `DS.api.postJson()` | No body needed, mixed real status codes — the exact case `DS.api` was built for |
| `forget.html` (all 4 stages) | `fetch()` + form-encoded body for send/verify/reset; `DS.api.postJson()` for resend | Same reasoning as `verify_otp.html`, applied per-endpoint based on whether it reads a body field |

## Visual/UX consistency across the four pages

- Same card width class pattern (`.ds-card` with an inline
  `max-width`), same header treatment (h1 + secondary-text subtitle),
  same button/spinner pattern, same `.ds-alert-danger` /
  `.ds-alert-success` banner placement directly under the header.
- `verify_otp.html` and `forget.html` share the identical OTP-box CSS
  block (6× `.otp-input`, paste/arrow-key/backspace handling) — copied
  intentionally rather than factored into a third shared JS file, since
  Phase 3B's instructions were explicit that only pages 1-4 were in
  scope for this pass; extracting a shared `otp-input.js` is a
  reasonable Phase 4-scope follow-up, not done here to avoid unrequested
  refactor risk this late in the phase.
- Password-visibility toggle markup/behavior is identical across
  `login.html`, `signup.html`, and `forget.html`.

## Known architectural limitation (not fixed, documented)

`partials/nav_public.html` supports an `active_nav` context variable
for `aria-current="page"` highlighting, but none of the four auth
views (`user_login`, `user_signup`, `forgot_page`, and — N/A for
`verify_otp`/OTP-adjacent, since they're one step past nav-relevant)
pass it. Fixing this means adding a context key to a `render()` call in
`accounts/views.py`, which is a backend change; per this phase's Rule 10
("do NOT rebuild backend unnecessarily... unless this audit discovers a
concrete auth-related defect") this was judged a cosmetic nav-highlight
gap, not a defect, and left alone. Documented so it isn't mistaken for
an oversight.
