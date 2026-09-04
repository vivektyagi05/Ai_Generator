# PHASE 3B AUTH — FLOW

All flows below are VERIFIED: each is covered by an automated test that
actually runs (see `accounts/tests/test_phase3b_e2e_flows.py` for
Flows A/B/C/D/E/F specifically, plus the pre-existing
`test_signup.py`/`test_otp_service.py`/`test_forgot_password.py`/
`test_forgot_password_frontend.py`/`test_security.py` for the backend
contract each flow rides on). None of this is inferred from reading the
templates alone.

## FLOW A — new user

```
public page → "Create account" ({% url 'signup' %})
  → POST /signup/  (real form, name+email+password+confirmPassword)
  → 302 → /verify-otp/         [PendingSignup created, no User yet]
  → GET /verify-otp/            (email shown from session, not client input)
  → POST /verify-otp/ {otp}     (fetch+FormData)
  → {"status":"success"}        [User created, PendingSignup deleted, session
                                  auth cookie set — verify_otp() calls
                                  django.contrib.auth.login() internally]
  → JS redirects to {% url 'home' %}
  → GET /home/  → 200, authenticated
```
VERIFIED: `FlowANewUserSignupToHome.test_full_signup_to_authenticated_home`.

## FLOW B — existing user

```
public page → "Log in" ({% url 'login' %})
  → POST /login/ (real form, email+password[+next])
  success → 302 to next (if safe) or "home"; session auth cookie set
  failure → 200, same page re-rendered, ONE generic
            "Invalid email or password" — identical for wrong-password
            and no-such-user (no enumeration signal here either)
```
VERIFIED (success, failure/no-enumeration, safe `next`, unsafe `next`
rejected): `FlowBExistingUserLogin.*`.

## FLOW C — OTP (signup)

```
6 digits, numeric-only boxes, paste-fills-all-6, arrow/backspace nav
wrong code   → {"status":"error","message":"Invalid verification code."} → boxes clear, refocus
expired code → {"...":"Verification code has expired. Please request a new one."}
too many (5) → {"...":"Too many incorrect attempts. Please request a new code."}
resend       → POST /resend-otp/ (DS.api.postJson, no body)
  429 within 30s of last send  → "Please wait a moment before requesting another code."
  429 after 5 sends in 60 min  → "Too many codes requested. Please try again later."
  200                          → {"status":"success"} → cooldown UX restarts
```
VERIFIED: `FlowCOtpNegativePaths.*` (wrong-then-right OTP, missing
session → redirect to signup, resend-before-cooldown → 429). The 5-min
expiry and 5-attempt limits were re-confirmed by direct code read of
`otp_service.py`/`otp_utils.py` (CODE-VERIFIED — exercising a literal
5-minute wait or 5 wrong guesses in an automated test would be slow and
was already covered by `test_otp_service.py`'s existing tests using
`freezegun`/manual attempt-count manipulation).

## FLOW D — forgot password

```
login page → "Forgot password?" ({% url 'forgot' %})
  → STATE A: enter email → POST /forgot/send-otp/
  → STATE B→C: identical generic response regardless of whether the
    account exists → OTP stage shown either way (enumeration-safe)
  → STATE C/D: enter 6-digit code → POST /forgot/verify-otp/
    STATE E (invalid) / STATE F (expired) → real otp_service message,
    HTTP status IS propagated here (400/403), unlike signup's verify
  → STATE H: verified → new password + confirm shown
  → STATE I: client-side length/match check (immediate feedback only)
  → STATE J: POST /forgot/reset-password/ {password}
  → STATE K: {"status":"password_reset"} → session.flush() server-side
    → NOT auto-logged-in → "Go to login" → {% url 'login' %}
  → old password now rejected, new password accepted at /login/
```
VERIFIED end-to-end including the loop back through the real login
endpoint with the new password:
`FlowDForgotPasswordFullJourney.test_full_forgot_password_journey`.

## FLOW E — authenticated navigation

```
login → home → profile (200) → logout (GET /logout/, any method — see
security audit §A) → 302 to /home/, session cookie cleared →
profile revisited → 302 to /login/ (login-required redirect, confirming
the session was genuinely destroyed, not just the nav link swapped)
```
VERIFIED: `FlowEAuthenticatedNavigation.test_authenticated_navigation_then_logout_returns_to_guest_state`.

## FLOW F — session edge cases

| Case | Backend behavior | Verified |
|---|---|---|
| Open `/verify-otp/` with no `signup_email` in session | 302 → `/signup/` | ✅ |
| POST `/verify-otp/` with no session at all | 200 `{"status":"error","message":"Session expired"}` | ✅ |
| POST `/resend-otp/` with no session | 400 `{"error":"Session expired"}` | ✅ |
| POST `/forgot/verify-otp/` without sending first | 400 `{"error":"Session expired"}` | ✅ |
| POST `/forgot/reset-password/` without a verified OTP | 403 `{"error":"OTP not verified"}` | ✅ |
| Authenticated user opens `/login/` | Renders the form anyway — **no redirect exists in the backend**; not invented on the frontend either | ✅ |
| Authenticated user opens `/signup/` | Same — renders the form, no backend guard | ✅ |
| `PendingSignup` older than 15 minutes, OTP still "valid" | Verify deletes the stale pending record and returns "Signup session expired. Please sign up again." — frontend then redirects to `/signup/` after 2s | ✅ |

All eight rows: `FlowFSessionEdgeCases.*` in
`test_phase3b_e2e_flows.py`.

## What Flow F reveals that wasn't fixed

Rows 6 and 7 (already-authenticated user can still reach `/login/` and
`/signup/`) are confirmed **existing backend behavior**, not a defect
introduced or left by Phase 3B — the views never guarded against it.
Per Rule 10, this wasn't "fixed" because it isn't demonstrably broken
(no security or data-consistency issue results — worst case, a logged-in
user resubmits the login form and gets redirected home again, or fills
out a signup form that would fail on `"Email already registered"` if
they used the same address). Documented here rather than silently
patched, since adding that redirect would be a backend behavior change
outside this phase's stated scope.
