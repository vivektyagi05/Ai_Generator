# AI Generators — Email Infrastructure Migration (Gmail SMTP → Brevo API)

## 1. Migration Summary

The project previously sent OTP/transactional email via Django's SMTP backend
(`EMAIL_BACKEND = django.core.mail.backends.smtp.EmailBackend`, Gmail on port
587). That fails on Render because outbound SMTP is blocked there. All SMTP
code has been removed and replaced with a layered, provider-agnostic
architecture calling Brevo's transactional email REST API over HTTPS:

```
accounts/views.py (signup, resend, forgot-password, profile)
    -> accounts/email_service.py           (Facade — public functions only)
        -> accounts/email/service.py       (EmailService — orchestration)
            -> accounts/email/validators.py    (email + header-injection checks)
            -> accounts/email/templates.py     (HTML rendering)
            -> accounts/email/retry.py          (exponential backoff, transient-only)
            -> accounts/email/providers/brevo_provider.py  (Brevo REST call)
                -> https://api.brevo.com/v3/smtp/email -> Internet -> Inbox
```

`accounts/email/config.py` loads all settings from environment variables only
(nothing hardcoded), and `accounts/email/exceptions.py` defines a full
exception hierarchy so transient failures (timeouts, 5xx, rate limits) are
retried and permanent ones (bad address, bad credentials) fail immediately.

**Why Brevo over Resend:** Resend requires you to add and verify a DNS
domain before it will send to real recipients — until then you're limited to
its own sandbox address. Brevo only requires verifying a sender **email
address** (no domain/DNS ownership needed), which works immediately with an
address like `ai.generatormails@gmail.com`, and its free tier (300/day, no
monthly cap) comfortably covers this project's OTP volume.

**A deliberate behavior change, and why:** the original code fire-and-forgot
3 of its 4 email sends in background threads with no error handling at all —
if SMTP failed, the user would land on the OTP-entry page with no code ever
arriving, silently. Since OTP delivery is required for signup/reset to
proceed, the new code sends OTP emails **synchronously** and returns a clean
`502 {"error": "..."}` response if Brevo is unreachable after retries,
instead of leaving the user stuck with an unusable page. Welcome and
password-changed notifications remain best-effort and never block or fail
the request — exactly as the spec asked.

## 2. Files Added

```
accounts/email/__init__.py
accounts/email/config.py            — env-var configuration loader
accounts/email/exceptions.py        — exception hierarchy (retryable vs. not)
accounts/email/validators.py        — email validation, header-injection prevention
accounts/email/templates.py         — HTML templates (OTP, welcome, password-changed)
accounts/email/retry.py             — exponential-backoff retry system
accounts/email/service.py           — EmailService orchestrator (validate→render→retry→log)
accounts/email/providers/__init__.py
accounts/email/providers/base.py            — EmailProvider interface
accounts/email/providers/brevo_provider.py  — Brevo REST adapter
.env.example                        — did not exist before; added for onboarding
EMAIL_MIGRATION_CHANGELOG.md        — this file
```

## 3. Files Modified

| File | What changed | Why |
|---|---|---|
| `accounts/email_service.py` | Completely rewritten as a thin facade exposing `send_registration_otp(email, otp)`, `send_reset_otp(email, otp)`, `send_welcome_email(email, name)`, `send_password_changed_email(email)`. No more SMTP code. | This was the file the task named for replacement; its previous content (`from django.core.mail import send_mail` + a stray line) was effectively non-functional — `views.py` imported a `send_email_async` that didn't exist in it. |
| `accounts/views.py` | Removed `import threading`, `from django.core.mail import send_mail` (both occurrences), `from .email_service import send_email_async`. Added `from .email_service import send_registration_otp, send_reset_otp, send_welcome_email, send_password_changed_email, EmailServiceError`. Updated all 4 email call sites (signup, resend-OTP, forgot-send-OTP, forgot-resend-OTP) to call the new typed functions with try/except → 502 on failure. Added best-effort `send_welcome_email()` after OTP verification creates the user, and best-effort `send_password_changed_email()` after both the forgot-password reset and the in-app profile password change. Removed one leftover debug `print("EMAIL_BACKEND:", ...)` line (referenced a setting that no longer exists). | Required to actually use the new email architecture; OTP logic itself (generation, `EmailOTP` model usage, session handling, verification) is untouched. |
| `AI_GENERATORS/settings.py` | Removed `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`. Added a comment pointing to the new env-var-driven config. | Spec explicitly required removing all SMTP configuration. `RESEND_API_KEY` (already present, already unused anywhere in the codebase) was left untouched. |
| `requirements.txt` | Re-saved as UTF-8 (was UTF-16-encoded) — **no package or version changed**. `requests` was already listed, so no new dependency was needed for the Brevo HTTP calls. | The file's UTF-16 encoding would very likely make `pip install -r requirements.txt` fail on Render (pip expects UTF-8). This sat outside the email system itself, but directly blocks "must run on Render," so it was fixed as a necessary side-fix. |
| `.env` | Removed `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD`-style SMTP vars (there were none actually present — only Django/Groq vars existed). Added the 7 new email env vars (see below). | Required configuration for the new provider. |

## 4. Files Removed

- No files were deleted outright; `accounts/email_service.py` was replaced in place (same path, new content) so `views.py`'s import path didn't need to change.
- SMTP-specific settings lines in `settings.py` were removed (listed above) — not full files, just those lines.

## 5. New Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `EMAIL_PROVIDER` | Yes | Currently must be `brevo` |
| `BREVO_API_KEY` | Yes | Your Brevo API key |
| `EMAIL_FROM` | Yes | Sender address, must be verified in Brevo |
| `EMAIL_FROM_NAME` | No (defaults to `AI Generators`) | Display name on outgoing mail |
| `EMAIL_TIMEOUT_SECONDS` | No (default `10`) | HTTP timeout per Brevo request |
| `EMAIL_MAX_RETRIES` | No (default `3`) | Max attempts for transient failures |
| `EMAIL_RETRY_BASE_DELAY` | No (default `1`) | Base seconds for exponential backoff |

Removed: `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (referenced in the old `settings.py`, never actually present as real secrets in the uploaded `.env`).

## 6. Production Deployment Steps

1. Create a free Brevo account at https://www.brevo.com.
2. Under **Senders, Domains & Dedicated IPs → Senders**, add and verify the address you'll send from (e.g. `ai.generatormails@gmail.com`) — Brevo emails you a confirmation link, no DNS changes needed.
3. Under **Settings → SMTP & API → API Keys**, generate a new API key.
4. Set the 4 required env vars (`EMAIL_PROVIDER`, `BREVO_API_KEY`, `EMAIL_FROM`, `EMAIL_FROM_NAME`) locally in `.env` for testing.

## 7. Render Deployment Steps

1. In the Render dashboard, open your Web Service → **Environment**.
2. Add: `EMAIL_PROVIDER=brevo`, `BREVO_API_KEY=<your key>`, `EMAIL_FROM=<verified sender>`, `EMAIL_FROM_NAME=AI Generators`.
3. Remove the old `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` Render env vars if they exist there (they're no longer read anywhere).
4. Redeploy. Because Brevo is called over HTTPS (port 443) rather than SMTP (port 587), Render's outbound-SMTP block no longer applies.
5. Trigger one real signup on the live Render URL and confirm the OTP email arrives.

## 8. Verification Checklist

- ✅ `python manage.py check` passes cleanly, both with and without email env vars set (config loads lazily — a missing `BREVO_API_KEY` only fails the first actual send, not app/`manage.py` startup).
- ✅ All new/modified files byte-compile (`python -m py_compile`).
- ✅ Mocked Brevo responses confirm correct exception mapping: 201→success, 401/403→`AuthenticationFailedError` (no retry), 400→`InvalidEmailError` (no retry), 429→`RateLimitError` (retried), 500+→`ProviderUnavailableError` (retried up to `EMAIL_MAX_RETRIES`), connection/timeout→`NetworkTimeoutError` (retried).
- ✅ Full Django test-client run through the real URL routes with Brevo mocked:
  - `POST /signup/` → OTP email sent with correct subject → OTP stored in `EmailOTP` → `POST /verify-otp/` → user created, logged in, welcome email sent.
  - `POST /forgot/send-otp/` → `POST /forgot/verify-otp/` → `POST /forgot/reset-password/` → password updated, password-changed email sent.
  - Simulated Brevo outage during `forgot_send_otp` → clean `502 {"error": "..."}` JSON response, not a Django 500 crash.
- ✅ No changes to URL routes, response JSON shapes on success, `EmailOTP`/`UserProfile`/`ChatHistory` models, JWT/session auth, or frontend templates.
- ⚠️ Not verified here (needs your Brevo account + Render deployment): an actual live send to a real inbox, and a live Render deployment run — I don't have a Brevo API key or network access to `api.brevo.com` in this sandbox.

## 9. Manual Configuration Required Inside Brevo

1. Verify your sender email address (Senders & IP → Senders → Add a sender). This is the only mandatory manual step — no domain/DNS work required to start sending.
2. (Optional, recommended later) Add and verify your own domain under **Domains** for better inbox placement / deliverability, and to send from `noreply@yourdomain.com` instead of a Gmail address — this needs zero code changes, just flipping `EMAIL_FROM`.
3. Generate an API key under Settings → SMTP & API → API Keys, and put it in `BREVO_API_KEY`.
4. Free-tier Brevo caps at 300 emails/day — keep an eye on volume as signups grow.
