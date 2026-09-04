# PHASE 3B AUTH — SECURITY AUDIT

## A. CSRF

| Endpoint | Mechanism | Status |
|---|---|---|
| `/login/`, `/signup/` | `{% csrf_token %}` in a real `<form>` | VERIFIED — Django's own CSRF middleware enforces this; `test_security.py` already covers rejection of missing/bad tokens |
| `/verify-otp/`, `/forgot/send-otp/`, `/forgot/verify-otp/`, `/forgot/reset-password/` | `X-CSRFToken` header from `DS.api.getCsrfToken()`, body sent as `application/x-www-form-urlencoded` via manual `fetch` | CODE-VERIFIED — confirmed the header is attached on every one of these calls by reading each page's script block |
| `/resend-otp/`, `/forgot/resend-otp/` | `DS.api.postJson()`, which attaches `X-CSRFToken` automatically for any unsafe method | VERIFIED via `test_security.py` and the new E2E flow tests, which exercise these through the real Django CSRF middleware (test client enforces it unless explicitly disabled, and it was not disabled here) |
| `/logout/` | No CSRF check today — the view has no `@require_POST`, so a GET (as this project's `logout` links already use) isn't CSRF-checked by Django at all (CSRF only applies to unsafe methods) | CODE-VERIFIED, not changed — see §H for the reasoning; this is existing backend behavior, not something the frontend can fix without a backend change, and is low-severity (logout has no destructive value to an attacker beyond forcing a logout) |

No CSRF exemption (`@csrf_exempt`) exists anywhere in `accounts/views.py`
for these endpoints — confirmed by direct grep, not assumption.

## B. Account enumeration

`forgot_send_otp` / `forgot_resend_otp` return the identical response
body regardless of account existence (see
`PHASE_3B_AUTH_BACKEND_CONTRACT.md` §7-8). Verified at both layers:

- Backend: `test_forgot_password.py::test_enumeration_resistant_response_bodies_match` (pre-existing, still passing)
- Frontend: `forget.html`'s JS never branches on, inspects, or displays
  anything from the response beyond the literal `message` string — no
  `if (email exists)` logic exists anywhere in the page. Confirmed by
  reading the full script block for this audit (CODE-VERIFIED).

## C. Open redirects

Only `login.html` handles a redirect target (`next`), and only by
passing it through as a hidden form field to the backend, which
validates it with `url_has_allowed_host_and_scheme` before using it —
no client-side URL construction or `window.location` redirect using
`next` exists anywhere in the frontend.

VERIFIED: `FlowBExistingUserLogin.test_safe_next_redirect_honored` and
`test_unsafe_next_redirect_rejected` (an external
`https://evil.example.com/steal` value is rejected server-side; the
frontend never even sees the resulting URL to second-guess it).

## D. Client-side security theater — removed, confirmed absent

Grepped all four current auth pages: zero hits for SQL-injection regex
checks, fake sessionStorage/localStorage attempt counters, fake
"verified" flags, or any client-side authentication-state assertion.
Every one of these existed in at least one of the four original pages
(see each page's own header comment / the individual Step reports) and
was removed during that page's own rebuild — this pass re-confirms
none crept back in and none exist in the two non-auth pages that were
touched (`profile.html`, `history.html`, `main.html` — those three
changes were nav-link URL fixes only, not security-relevant).

## E. Password handling

- Never sent in a query string or URL — always POST body
  (`password=` + `encodeURIComponent(...)`, or the real `<form>` for
  login/signup).
- Never written to `localStorage`/`sessionStorage`/`console.log` —
  confirmed by grep across all four pages.
- Never rendered back into HTML (no `{{ password }}` anywhere in any
  template, confirmed by grep).
- The only place a password-derived value crosses a boundary
  client-side is the visibility toggle, which only changes the
  `type` attribute of the existing `<input>` — no copy of the value is
  made.

## F. OTP handling

- Never placed in a URL (always POST body).
- Never logged to the console.
- Never stored in `localStorage`/`sessionStorage`.
- The frontend never accepts or trusts a "verified" state from
  anywhere except a literal `{"status":"success"}` /
  `{"status":"verified"}` JSON response from the real endpoint in the
  same request — there is no persisted/cached client-side "I already
  verified" flag that could be replayed or forged.

## G. Session security

- Login: sets Django's real session cookie via
  `django.contrib.auth.login()` — nothing client-side participates in
  establishing this.
- Logout: `django.contrib.auth.logout()` clears it server-side; frontend
  navigates to it, doesn't simulate it.
- Password reset: `request.session.flush()` on success — VERIFIED the
  frontend does not assume an authenticated state afterward (no
  auto-redirect to `/home/`, no client-side "logged in" flag set) —
  `FlowDForgotPasswordFullJourney` and
  `ForgetPageFrontendTests.test_user_not_authenticated_after_successful_reset`
  both assert `_auth_user_id` is absent from the session after reset.

## H. XSS

Every dynamic value written to the DOM across all four pages uses
`.textContent` (never `.innerHTML` with interpolated data — the only
`.innerHTML` writes left anywhere are static spinner markup with zero
interpolated content, confirmed by grep in the forensic findings). No
template uses `|safe` on any user-influenced value. The one
server-rendered dynamic value (`{{ email }}` in `verify_otp.html`,
`{{ email }}` in `forget.html`'s hidden state) goes through Django's
default auto-escaping — neither template opts out of it.

## I. Duplicate submission

Every submit path in all four pages disables its button and swaps in a
spinner for the duration of the in-flight request (form pages: on
`submit`; fetch-based pages: an explicit `verifying`/`resetting`
boolean guard in addition to the disabled button, since a fetch-based
page can receive a second click before the first `disabled = true`
paints). None of this is presented as a security control — every one
of these pages' header comments explicitly say the *backend* remains
authoritative (e.g. Flow D's duplicate-reset-after-completion test
proves the server, not the button, is what actually prevents a replay
from succeeding — see
`ForgetPageFrontendTests.test_replaying_reset_after_completion_fails_cleanly_not_silently`).

## Summary

No new vulnerability was introduced. No CSRF gap exists in any endpoint
this phase touched. Enumeration resistance is preserved end-to-end,
frontend included. The one pre-existing minor finding (§A, `/logout/`
has no CSRF check because it accepts GET) is a backend characteristic,
not something Phase 3B's frontend-only scope could fix, and is
low-severity — documented rather than silently left unmentioned.

**SECURITY: PASS** (one pre-existing, low-severity, documented,
out-of-scope-for-this-phase item: `/logout/` accepts GET).
