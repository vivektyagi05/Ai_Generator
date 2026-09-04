# PHASE 3B — AUTHENTICATION & ONBOARDING BACKEND CONTRACT

Status: **VERIFIED** — every endpoint below was read directly from
`accounts/views.py`, `accounts/urls.py`, `accounts/otp_service.py`,
`accounts/otp_utils.py`, `accounts/rate_limit.py`, and `accounts/models.py`
in the uploaded project. Nothing here is inferred from naming conventions.

> Naming note: this project already has a completed, unrelated **Phase 3**
> (Subscriptions/billing — `PHASE_3_COMPLETION_AUDIT.md`,
> `PHASE_3_FORENSIC_FINDINGS.md`, `PHASE_3_SUBSCRIPTION_ARCHITECTURE.md`).
> To avoid overwriting that work, this auth/onboarding effort is filed as
> **Phase 3B** and its docs are prefixed `PHASE_3B_AUTH_*`.

## 0. Cross-cutting facts

- **Style**: these are classic Django views, not a JSON REST API. `GET`
  requests that show a page return rendered HTML (`render(...)`). `POST`
  requests are a mix: `user_login` / `user_signup` re-render the same
  template with `{"error": ...}` in context on failure and use
  `redirect(...)` on success (never JSON). `verify_otp`, `resend_otp`, and
  every `forgot_*` endpoint return `JsonResponse` on `POST` regardless of
  outcome.
- **CSRF**: no view is `csrf_exempt`. Standard Django CSRF applies to every
  `POST`. `static/js/api.js` (`DS.api`) already reads the `csrftoken` cookie
  (falling back to a `<meta name="csrf-token">` tag) and attaches
  `X-CSRFToken` to unsafe requests — this is the correct utility to reuse
  for the JSON endpoints below. The two form-post endpoints (login, signup)
  should keep using Django's `{% csrf_token %}` template tag since they are
  real form submissions, not fetch calls.
- **Session**: Django's default session/cookie auth. `request.session` is
  used as transient server-side state (`signup_email`, `reset_email`,
  `otp_verified`) — never as a place holding a password or an OTP value.
- **OTP lifecycle** (`accounts/otp_service.py`, `otp_utils.py`, model
  `EmailOTP`):
  - 6-digit numeric, generated with `secrets.randbelow` (CSPRNG).
  - One row per `(email, purpose)` — issuing a new code overwrites the
    existing row and resets `attempts` to 0 and the expiry clock.
  - **Expiry: 5 minutes** from `created_at` (`EmailOTP.is_expired`).
  - **Max attempts: 5** (`OTP_VERIFY_MAX_ATTEMPTS`) — the 6th wrong guess
    (or any guess once attempts ≥ 5) deletes the record and returns a
    "too many incorrect attempts" error (`403`), forcing a fresh
    resend/issue.
  - Comparison is constant-time (`hmac.compare_digest`).
  - On success the record is deleted immediately (no replay).
- **Rate limiting** (`accounts/rate_limit.py`, LocMemCache-backed — see
  file docstring for the known multi-worker caveat):
  - **Send cooldown: 30s** per `(email, purpose)` between OTP sends.
  - **Send window cap: 5 sends / 60 minutes** per `(email, purpose)`.
  - **Per-IP cap: 20 requests / 10 minutes** per action name (`signup`,
    `resend_otp`, `forgot_send_otp`, `forgot_resend_otp` are each their own
    action bucket).
  - Cooldown/window failures on signup/resend surface as a normal `error`
    string (200 for the signup form re-render, or a `400/429` JSON error for
    resend) — the UI must not imply the OTP was sent when these fire.
- **Pending signup** (`PendingSignup` model): holds `email`, `name`,
  `password_hash` (hashed once via `make_password`, never the plaintext)
  server-side while OTP is outstanding. **Expires after 15 minutes**
  (`PendingSignup.is_expired`) — checked in `verify_otp`'s `POST` branch;
  an expired pending record is deleted along with any outstanding
  `EmailOTP` row and forces the user back to signup.
- **Forgot-password enumeration resistance**: `forgot_send_otp` and
  `forgot_resend_otp` always return the same generic JSON body
  (`_FORGOT_SEND_GENERIC_RESPONSE`, `{"status": "otp_sent"/"otp_resent", ...}`)
  whether or not the email is registered, and whether or not it was
  actually rate-limited. The frontend must not infer "account exists" or
  "code definitely sent" from this response beyond its literal text.
- **Redirects**: `user_login` accepts `?next=` (GET) or `next` (POST field),
  validated with `url_has_allowed_host_and_scheme` before use — this is the
  only safe-redirect mechanism in the codebase; the frontend should keep
  forwarding `next` through the login form/link exactly as today (no new
  client-side redirect logic).

---

## 1. `POST /login/` (name: `login`) — also `GET`

| | |
|---|---|
| Method | GET (show form), POST (submit) |
| Request fields (POST, form-encoded) | `email`, `password`, `next` (optional) |
| CSRF | Required (`{% csrf_token %}`) |
| Validation | Both fields required (else re-render with error) |
| Success | `django.contrib.auth.login()`, then `redirect(next or "home")` — **302, not JSON** |
| Failure | Re-renders `login.html` with `{"error": "...", "next": ...}` — **200, not JSON** |
| Error messages | `"Email and password are required"` / `"Invalid email or password"` (deliberately generic — does not distinguish "no such user" from "wrong password") |
| Rate limiting | **None on this endpoint today.** No lockout, no throttle. (Frontend must not claim otherwise.) |
| Account-inactive / email-unverified state | **Not implemented.** `authenticate()` uses Django's default backend, which already excludes `is_active=False` users, but there is no distinct error message for that case — it falls into the same generic "Invalid email or password". Do not build a separate "account inactive" UI state; there is no backend signal for it. |
| Session | Standard Django session cookie set on success |

## 2. `POST /signup/` (name: `signup`) — also `GET`

| | |
|---|---|
| Method | GET (show form), POST (submit) |
| Request fields | `name`, `email`, `password`, `confirmPassword` |
| CSRF | Required |
| Validation order | all-required → password==confirmPassword → email not already a `User.username` → Django `validate_password` → IP throttle (`signup` action) → send cooldown/window (`signup` purpose) |
| Duplicate email | `"Email already registered"` |
| Password policy errors | Django's `validate_password` messages, joined with a space |
| Email delivery failure | `send_registration_otp` raising `EmailServiceError` → `"Could not send verification code. Please try again in a moment."` (form re-render, not JSON) |
| Success | Creates/updates `PendingSignup` (hashed password only), issues OTP, sends email, sets `session["signup_email"]`, **redirects to `verify_otp`** — no user row is created yet |
| Failure | All failures re-render `signup.html` with `{"error": ...}` — **200, not JSON** |

## 3. `GET`/`POST /verify-otp/` (name: `verify_otp`)

| | |
|---|---|
| GET | Requires `session["signup_email"]` (else redirect to `signup`); renders `verify_otp.html` with `{"email": email}` |
| POST fields | `otp` |
| CSRF | Required |
| Response format | **Always JSON**, even on the "no email/otp" case: `{"status": "error", "message": "Session expired"}` |
| Pending-expired | If `PendingSignup` missing or `is_expired()` (>15 min), the pending row + any `EmailOTP` are deleted, session key removed, `{"status":"error","message":"Signup session expired. Please sign up again."}` |
| Invalid/expired OTP | Delegates to `otp_service.verify_otp` → message is one of: `"Verification code is required."`, `"Verification code not found or already used. Please request a new one."`, `"Verification code has expired. Please request a new one."`, `"Too many incorrect attempts. Please request a new code."`, `"Invalid verification code."` — **all returned as `{"status":"error","message": ...}`, HTTP 200** (the view does not propagate `otp_service`'s status_code) |
| Success | Creates the real `User` (username=email) using `pending.password_hash` directly (not re-hashed), logs the user in, deletes `PendingSignup`, clears `session["signup_email"]`, best-effort welcome email, returns `{"status": "success"}` |
| Note for frontend | Since all POST responses are `200` with a `status` field, the UI must branch on `payload.status === "success"` vs `"error"` — not on HTTP status code. |

## 4. `POST /resend-otp/` (name: `resend_otp`)

| | |
|---|---|
| Method | POST only (`@require_POST` → 405 on GET) |
| Fields | none (uses `session["signup_email"]`) |
| No session | `{"error": "Session expired"}`, 400 |
| No pending signup | `{"error": "Signup session expired. Please sign up again."}`, 400 |
| IP throttle (`resend_otp` action) | `{"error": "Too many requests. Please try again later."}`, 429 |
| Cooldown (30s) | `{"error": "Please wait a moment before requesting another code."}`, 429 |
| Window cap (5/hr) | `{"error": "Too many codes requested. Please try again later."}`, 429 |
| Email send failure | `email_error_response(exc)` — shape defined in `accounts/email/error_mapping.py` (structured error, not the plain `{"error": str}` shape) |
| Success | `{"status": "success"}`, 200 |
| Note | Error-body shape is **inconsistent**: most errors are `{"error": "<string>"}`, but the email-failure path returns whatever `email_error_response` produces. `DS.api`'s `extractErrorMessage` already handles both `payload.error` (string) and `payload.error.message` (object) shapes — use it rather than assuming one shape. |

## 5. `POST /logout/` (name: `logout`)

| | |
|---|---|
| Method | Any (no `@require_POST`) |
| CSRF | N/A for GET; if triggered via link (GET) there's no CSRF check today. Frontend should still POST it via a button, not a bare `<a href>`, since a GET-triggered state change is a general anti-pattern even though the current view doesn't enforce CSRF here. |
| Response | `logout(request)` then `redirect('home')` — **302, not JSON** |

## 6. `GET /forgot/` (name: `forgot`)

Renders `forget.html`. No context.

## 7. `POST /forgot/send-otp/` (name: `forgot_send_otp`)

| | |
|---|---|
| Method | POST only |
| Fields | `email` |
| Missing email | `{"error": "Email required"}`, 400 |
| IP throttle (`forgot_send_otp`) | `{"error": "Too many requests. Please try again later."}`, 429 |
| Otherwise | **Always** sets `session["reset_email"]` and `session["otp_verified"]=False`, and **always** returns `{"status": "otp_sent", "message": "If an account exists for this email, a reset code has been sent."}` — regardless of whether the account exists, whether it was cooldown/window-throttled, or whether the email actually sent. This is intentional enumeration resistance; do not add UI copy that contradicts it (e.g. don't say "check your email — your account was found"). |

## 8. `POST /forgot/resend-otp/` (name: `forgot_resend_otp`)

| | |
|---|---|
| No `session["reset_email"]` | `{"error": "Session expired"}`, 400 |
| IP throttle (`forgot_resend_otp`) | `{"error": "Too many requests. Please try again later."}`, 429 |
| Otherwise | Same generic-response pattern as above: `{"status": "otp_sent", "message": "..."}` (same constant used for both send and resend on the generic paths) — but on the true "OTP actually issued and sent" success path it returns `{"status": "otp_resent"}` instead. Frontend should treat both `otp_sent` and `otp_resent` as "assume a code may have been sent; show the standard resent state" rather than branching UI on which one came back. |

## 9. `POST /forgot/verify-otp/` (name: `forgot_verify_otp`)

| | |
|---|---|
| Fields | `otp` |
| No `reset_email` in session or no `otp` | `{"error": "Session expired"}`, 400 |
| Invalid/expired/too-many-attempts | `{"error": "<otp_service message>"}`, status code **from `otp_service`** (400 or 403) — unlike `/verify-otp/`, this endpoint DOES propagate the real status code |
| Success | Sets `session["otp_verified"] = True`, returns `{"status": "verified"}`, 200 |

## 10. `POST /forgot/reset-password/` (name: `forgot_reset_password`)

| | |
|---|---|
| Guard | Requires `session["otp_verified"]` truthy, else `{"error": "OTP not verified"}`, 403 |
| Fields | `password` |
| No `reset_email` | `{"error": "Session expired"}`, 400 |
| Password policy | Django `validate_password` → `{"error": "<messages>"}`, 400 |
| User missing (edge case) | Session flushed, `{"error": "Password reset failed. Please start over."}`, 400 |
| Success | `set_password`, save, **`request.session.flush()`** (fully destroys the session — user is NOT auto-logged-in), best-effort "password changed" email, `{"status": "password_reset"}`, 200 |
| Frontend implication | After success, the user must be routed to `/login/` (not treated as authenticated) — the backend explicitly ends the session. |

---

## 11. What does NOT exist in the backend (do not build UI for these)

- No "continue without login" / guest-checkout endpoint tied to auth pages
  specifically (guest AI usage is handled elsewhere, via
  `accounts/services/guest_identity.py`, unrelated to these auth views).
- No distinct "account inactive" or "email not verified, please verify"
  error on `/login/` — see §1.
- No password-strength-scoring endpoint — any strength meter must be
  client-side-only cosmetic feedback, not a validation source of truth
  (the real validation is `validate_password` server-side).
- No lockout/attempt-count on `/login/` itself (only the OTP endpoints have
  attempt limits).
- No CSRF exemption anywhere — every fetch-based POST must send
  `X-CSRFToken` via `DS.api`.
