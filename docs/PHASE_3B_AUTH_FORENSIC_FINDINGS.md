# PHASE 3B AUTH — FORENSIC FINDINGS

Fresh repository-wide audit performed at final-integration time (not
copied from the per-page reports written during Steps 1-4). Every
finding below was re-confirmed against the current source, not assumed
from earlier documentation.

## 1. Backend — unchanged, confirmed sound

`accounts/views.py` (login, signup, verify_otp, resend_otp, logout,
forgot_page, forgot_send_otp, forgot_resend_otp, forgot_verify_otp,
forgot_reset_password), `otp_service.py`, `otp_utils.py`,
`rate_limit.py`, `models.py` (`PendingSignup`, `EmailOTP`) were re-read
in full for this pass. No discrepancy was found against
`PHASE_3B_AUTH_BACKEND_CONTRACT.md` (written in Step 1) or the
per-page findings in the login/signup/verify_otp/forget commit
comments. **No backend defect was found, so no backend file was
touched in this final pass** (Rule 10).

One pre-existing, out-of-scope defect was re-confirmed, not fixed:
`test_ai_credit_exhaustion_ui.py`'s 5 failures are a `/api/ai/`
(AI-generator endpoint) issue unrelated to authentication — same 5,
same file, same error (`500` instead of the expected structured status)
before and after every Phase 3B change. Left alone per Rule 10 ("no
speculative backend rewrites... unless this audit discovers a concrete
auth-related defect" — this isn't one).

## 2. Auth frontend — CODE-VERIFIED, one file at a time (Steps 1-4)

`login.html`, `signup.html`, `verify_otp.html`, `forget.html` were each
individually rebuilt, checked, and test-verified in prior steps of this
Phase 3B effort. Re-reading them for this pass found no regression and
no leftover issue from the original standalone versions (particle
animations, independent color palettes/fonts, `alert()`, SQL-injection
regex theater, fake sessionStorage rate limiters, hardcoded `/login/`
`/home/` paths, duplicated CSRF-token readers) — all were already
removed page-by-page; see each page's own header comment block for the
itemized removal list.

## 3. Shared frontend — confirmed as designed, still partially unadopted

- `base_public.html`, `tokens.css`, `components.css`, `static/js/api.js`,
  `static/js/toast.js` — all four auth pages now load and use these
  (Phase 2's stated goal for "Phase 3"). `toast.js` (`DS.toast`) is
  loaded on every auth page via `nav_public.html` but **not actually
  called** by any of the four pages — each page uses its own inline
  `ds-alert` banner instead of a toast for form-level errors, which is
  the more correct pattern for a persistent, in-form validation message
  (a toast is better suited to transient, page-level notifications).
  This is a legitimate design choice, not an oversight — documented
  here since the instructions called for a scan of unused JS.
- `static/js/nav.js` — powers the mobile nav toggle in `nav_app.html`
  (the authenticated shell); `nav_public.html` doesn't need it since it
  has no collapsible menu. Not touched, not needed by the auth pages.
- `base_app.html` / `nav_app.html` — audited, not modified. These back
  `main.html`/`profile.html`/`history.html`, not the four auth pages,
  and no defect was found in them.

## 4. Other entry points — genuine hardcoded-URL findings, fixed

Grepped every template for `href="/`, `action="/`, and
`window.location.href = "/` combined with an auth-related path
(`login`, `signup`, `logout`, `profile`, `history`, `home`). Found and
fixed (see §8 of the completion audit for the full list):

- `templates/main.html` — 5 hardcoded links (`/profile/`, `/history/`,
  `/logout/`, `/login/`, `/signup/`) in the guest/authenticated nav
  block → now `{% url %}`.
- `templates/profile.html` — 1 hardcoded `href="/"` and 2 hardcoded
  `window.location.href` assignments (`"/"`, `"/logout/"`) → now
  `{% url %}`.
- `templates/history.html` — 1 hardcoded `href="/"` → now `{% url %}`.

None of these were dead links (the paths were all correct today), so
this is a resilience/consistency fix, not a bug fix — but exactly what
the nav-audit step asked to check for. `templates/plans.html` and
`templates/partials/nav_app.html` already used `{% url %}` correctly
and needed no change.

## 5. Security theater sweep — clean

Grepped all four auth pages plus the three touched non-auth templates
for `alert(`, `TODO`, `FIXME`, `Coming soon`, `sessionStorage`,
`localStorage`, `innerHTML` with dynamic content, and SQL-injection-
style regex checks. Zero live hits — the only `innerHTML` usages left
are static, hardcoded spinner markup (`<span class="ds-spinner">...`)
with no interpolated data, and the only `alert(`/`sessionStorage`
occurrences left are inside code comments describing what was already
removed in the per-page steps.

## 6. Design-token duplication — resolved by the per-page migrations

All four auth pages now source colors/spacing/radii from
`tokens.css`'s single `:root` block via `base_public.html`. None of the
four independent `:root` blocks that existed in the original files
remain. `components.css`'s `.ds-*` classes (`.ds-card`, `.ds-field`,
`.ds-input`, `.ds-btn-*`, `.ds-alert-*`, `.ds-spinner`) are used
consistently across all four; the only page-specific CSS remaining per
page is the OTP-box layout (`verify_otp.html`/`forget.html`, identical
between the two) and small inline `style=""` attributes for layout,
matching the pattern `tokens.css`'s own docstring anticipated.
