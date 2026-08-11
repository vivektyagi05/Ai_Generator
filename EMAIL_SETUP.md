# Email Setup — Brevo Transactional API

This project sends all outbound email (signup OTP, password-reset OTP,
welcome email, password-changed notification) through **Brevo's**
transactional email REST API. It does not use SMTP and does not use any
other provider.

```
accounts/views.py
  -> accounts/email_service.py        (facade — the only import views use)
      -> accounts/email/service.py    (EmailService: validation, template, retry)
          -> accounts/email/providers/brevo_provider.py
              -> https://api.brevo.com/v3/smtp/email
```

## 1. Create a Brevo account

Sign up at <https://www.brevo.com>. The free tier is enough for OTP-volume
transactional email.

## 2. Create an API key

Brevo dashboard → **Settings → SMTP & API → API Keys → Generate a new API
key**. Copy it immediately — Brevo only shows it once. This is your
`BREVO_API_KEY`.

## 3. Verify a sender

Brevo dashboard → **Senders, Domains & Dedicated IPs → Senders → Add a
sender**. Enter the email address you want OTP/notification mail to come
from and complete the verification email Brevo sends to that address.

- You only need to verify a single **sender address**, not a DNS domain.
- Until a sender is verified, Brevo will reject send requests from that
  address — signup/password-reset will surface "Could not send
  verification code" rather than a silent failure or a false success.

## 4. Environment variables

Set these in `.env` locally, or in your host's environment settings (e.g.
Render → your service → Environment) in production. See `.env.example`
for the full list with placeholders — **never** commit real values.

| Variable | Required | Meaning |
|---|---|---|
| `EMAIL_PROVIDER` | yes | Must be `brevo` — no other provider is implemented. |
| `BREVO_API_KEY` | yes | The API key from step 2. |
| `EMAIL_FROM` | yes | The verified sender address from step 3. |
| `EMAIL_FROM_NAME` | no | Display name (default: `AI Generators`). |
| `EMAIL_TIMEOUT_SECONDS` | no | Per-request timeout to Brevo (default: `10`). |
| `EMAIL_MAX_RETRIES` | no | Max attempts for transient failures (default: `3`). |
| `EMAIL_RETRY_BASE_DELAY` | no | Base seconds for exponential backoff (default: `1`). |

If `BREVO_API_KEY` or `EMAIL_FROM` is missing, the app raises
`ConfigurationError` **only when an email is actually sent** — not at
Django startup, so `manage.py migrate`/`runserver` etc. still work without
email configured. See "Local setup" below.

## 5. Local setup

```bash
python -m venv .venv
source .venv/bin/activate  
pip install -r requirements.txt
cp .env.example .env        
python manage.py migrate
python manage.py runserver
```


You can run the app locally without a Brevo key configured — everything
except actually sending email will work, and any OTP-send attempt will
return a clean error instead of crashing.

## 6. Testing

```bash
python manage.py test
```

The suite mocks the Brevo HTTP call (`requests.post`) — it never makes a
real network request. See "Testing" section further below for what's
covered.

### Real email testing (manual)

To verify actual delivery once your sender/API key are set up:

1. Set real `BREVO_API_KEY` / `EMAIL_FROM` in `.env`.
2. `python manage.py runserver`, then sign up with a real inbox you control.
3. Confirm the OTP email arrives and check the Brevo dashboard
   (**Statistics → Transactional**) for the send.

This project's own automated test suite intentionally does **not** hit the
real Brevo API (that would make tests slow, flaky, and dependent on
network/credentials) — real delivery is a manual, one-time verification
step per environment.

## 7. Common Brevo errors

| HTTP status | Meaning | What the app does |
|---|---|---|
| 401 / 403 | API key rejected or lacks permission | `AuthenticationFailedError` → user sees "email service temporarily unavailable"; not retried. Check the key and that it hasn't been revoked. |
| 400 | Malformed request | Only classified as an invalid recipient address when Brevo's own error body says so; otherwise treated as our request/config problem (e.g. unverified sender), not the user's fault. Not retried. |
| 429 | Rate limited by Brevo | Retried with exponential backoff, up to `EMAIL_MAX_RETRIES`. |
| 5xx | Brevo-side outage | Retried with exponential backoff. |
| Timeout / connection error | Network issue reaching Brevo | Retried with exponential backoff. |
| Anything else | Undocumented/unexpected response | Treated as **non-retryable** — logged with full internal detail, user sees a generic error. We deliberately don't guess at unknown statuses. |

Diagnostics (status code, Brevo error code, request ID, latency) are always
logged server-side (see "Logging" below) — never shown to the end user and
never containing the API key, OTP, or password.

## 8. Rate-limiting behavior

Implemented in `accounts/rate_limit.py` using Django's cache framework
(no Redis/Celery dependency):

- **Per-email cooldown**: 30 seconds between OTP sends to the same
  (email, purpose).
- **Per-email window cap**: max 5 OTP sends per hour per (email, purpose).
- **Per-IP throttle**: max 20 auth-related requests per 10 minutes per
  client IP, on signup/resend/forgot-password endpoints.
- **Per-OTP attempt cap**: 5 wrong guesses before the OTP is invalidated
  (shared logic for both signup and password-reset OTPs —
  `accounts/otp_service.py`).

**Known limitation**: the default cache backend (`LocMemCache`) is
per-process. This app runs multiple gunicorn worker processes (see
`Procfile`), so each worker keeps its own counters — the effective limit in
production is roughly `configured limit × worker count`, and a client can
partially evade a single worker's cooldown by chance of load-balancing. For
airtight production rate limiting, point `CACHES` in `settings.py` at a
shared backend (e.g. a Redis-backed cache) — `accounts/rate_limit.py` needs
no code changes to benefit from that, since it only talks to Django's cache
API.

## 9. OTP behavior

- 6-digit numeric code, generated with `secrets` (cryptographically secure —
  see `accounts/otp_utils.py`), never `random`.
- Expires 5 minutes after issue (`EmailOTP.is_expired()`).
- One active OTP per `(email, purpose)`, enforced at the database level via
  a `UniqueConstraint` (not just application logic) — see migration
  `0005_pendingsignup_emailotp_unique_email_purpose`.
- Signup and password-reset OTPs share the same issuance/verification code
  path (`accounts/otp_service.py`), so they behave identically.
- On successful verification the OTP row is deleted immediately — no
  replay.

### Signup password handling

The signup flow never stores a plaintext password in the session. At
signup time the password is hashed once (`make_password`) into a
server-side `PendingSignup` row; the session only holds the email as a
pointer to that row. On successful OTP verification, the already-hashed
value is assigned directly to `User.password` (not re-hashed via
`set_password`/`create_user`) and the `PendingSignup` row is deleted.
`PendingSignup` rows expire after 15 minutes (see "Cleanup" below).

## 10. Production deployment notes (Render)

- Set all required env vars (section 4) in the Render service's
  **Environment** tab — never commit them.
- `runtime.txt` targets Python 3.12.
- `Procfile` runs `gunicorn AI_GENERATORS.wsgi` — multiple worker
  processes; see the rate-limiting caveat above.
- **Scheduled cleanup**: expired `EmailOTP`/`PendingSignup` rows don't
  delete themselves. Run:
  ```bash
  python manage.py cleanup_expired_otps
  ```
  on a schedule — e.g. a Render **Cron Job** service every 15 minutes
  against the same environment/database as the web service. Any other
  host's cron/scheduler works the same way; the command is idempotent and
  safe to run as often as you like.
- Consider a shared cache backend (see section 8) if you scale beyond a
  single worker/instance and want the rate limiting to be fully accurate.

## 11. Security warnings

- Never commit `.env`. `.env.example` contains placeholders only.
- `BREVO_API_KEY`, `SECRET_KEY`, OTP values, and passwords are never logged
  — see the logging config in `AI_GENERATORS/settings.py` and the
  `extra={...}` fields actually passed at each log call site in
  `accounts/email/`.
- Every EmailServiceError shown to the user comes from that exception
  class's `public_message` only — never the raw provider response or
  exception message (`accounts/email/error_mapping.py`).
- Forgot-password responses are deliberately identical whether or not the
  email is registered (see `accounts/views.py::forgot_send_otp`) —
  don't change this without re-checking the enumeration-resistance
  requirement.

## 12. Testing

`accounts/tests/` covers:

- `test_provider.py` — every documented Brevo status code, malformed
  success bodies, ambiguous 400s, network failures.
- `test_retry.py` — retryable vs non-retryable exceptions, backoff,
  max-attempt bound.
- `test_config.py` — env var loading and validation.
- `test_service.py` — EmailService orchestration via a fake provider.
- `test_otp_service.py` — issuance, expiry, attempts, replay, DB unique
  constraint.
- `test_signup.py` — full signup → verify → login flow, Brevo
  success/failure, no plaintext password anywhere, replay prevention.
- `test_forgot_password.py` — enumeration resistance, full reset flow,
  cooldowns, session cleanup.
- `test_security.py` — no secrets/OTP in logs or responses, CSRF
  enforcement, brute-force/rate-limit lockout.
- `test_error_mapping.py` — distinct HTTP status per exception type.
- `test_input_validation.py` — missing POST fields never 500.
- `test_models_and_cleanup.py` — model invariants, cleanup command.
- `test_rate_limit.py` — cooldown/window/IP-throttle unit behavior.

Run everything with `python manage.py test`.
