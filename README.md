# 🤖 AI Generator – Secure, Credit-Based AI SaaS Platform

A production-hardened Django SaaS platform that gives authenticated users access to **multiple AI generation tools** through a **credit-based entitlement system** — with OTP authentication, Razorpay subscription billing, usage tracking, history, and nine phases of production hardening behind it.

> ⚠️ **Note on scope:** this project has grown well beyond a single-endpoint AI chat demo. It now includes a transactional credit engine, plan-based entitlements, Razorpay-billed subscriptions, and audit-safe account deletion. See [Verification Status](#-verification-status) below for exactly what has and hasn't been independently re-verified in the current build.

## 🚀 Live Demo

👉 https://ai-generator-k573.onrender.com

## ✨ Key Features

### 🔐 Authentication & Security
- Secure signup, login, and session-based authentication
- Email OTP verification for signup and password reset (attempt-limited, time-boxed)
- IP-based **and** email-based login-throttling — brute-force protection that doesn't itself become an account-enumeration channel
- CSRF / XSS / IDOR audited across all state-changing routes
- Environment-based configuration (`.env`) — no secrets hardcoded

### 🤖 AI Generator Engine
- Five specialized generators — **Code, Story, Application, Proposal, Photo** — plus a base **Chat** capability
- All generators run through one shared, auditable lifecycle: validate → authorize → reserve credits → call provider → finalize/refund → save history
- Automatic credit refund on AI provider failure or timeout
- Idempotent request handling — a retried/duplicate request never double-charges

### 💳 Credit Engine
- Every credit movement is a `CreditTransaction` row — reserve, finalize, and refund are never in-place mutations
- `transaction.atomic()` + `select_for_update()` row locking prevent concurrent double-spend on one account
- Unique `(account, reference_id, transaction_type)` constraint guarantees idempotency on retried requests

### 🧩 Entitlements & Subscriptions
- Server-authoritative entitlement chain: `Plan → Feature → PlanFeatureRule → UserEntitlement → Generator Access`
- The frontend is never trusted to gate a feature — access is re-evaluated on every request
- Subscription state machine: `PENDING → ACTIVE → PAST_DUE → GRACE_PERIOD → CANCELLED/EXPIRED`, with invalid transitions rejected at the service layer

### 💰 Razorpay Billing
- Server-created orders, Razorpay-hosted checkout
- Every subscription activation is gated by a **signature-verified webhook**, never by a client-side "payment success" callback
- Full and partial refund lifecycle handled and recorded distinctly; unknown refund IDs are never silently ignored

### 🧑‍💻 User Dashboard & Profile
- Per-user AI usage history, with reopen-in-generator continuity
- Profile management, avatar upload (hardened against Pillow `DecompressionBombError`)
- Password change and **audit-safe account deletion**: personal data is anonymized and sessions revoked, but `Payment`, `CreditTransaction`, `AIUsage`, and `Subscription` records are preserved for financial/audit integrity — never cascade-deleted

### ⚡ Production Hardening (Phase 9)
- Redis-backed cache and rate limiting (`django-redis`), replacing per-worker `LocMemCache` fragmentation across Gunicorn processes, with a documented dev fallback
- Structured logging, frontend/backend contract audit, duplicate CSRF-reader removed from billing JS
- Clean-ZIP verification: fresh virtual environment, fresh dependencies, `manage.py check`, migration-drift check, and the full test suite — all re-run from a clean extraction, not just the working tree

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Language / Framework | Python · Django 5.2 |
| Frontend | Django Templates (HTML/CSS/JS), shared design tokens & app shell |
| Database | SQLite (dev/test) · PostgreSQL-ready architecture |
| Caching / Rate Limiting | Redis (`django-redis`), `LocMemCache` fallback in development |
| AI Providers | Groq / LLaMA-based text generation · Pollinations image generation |
| Payments | Razorpay (orders, checkout, signed webhooks) |
| Email | Brevo transactional email API (OTP & notifications) |
| Deployment | Gunicorn · WhiteNoise · Render |

## 🏗️ Architecture

```
                    User / Browser
                          │
                          ▼
             Django Templates / UI (HTML+CSS+JS)
                          │
                          ▼
                  Django Views / APIs
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  Authentication      AI Generation      Billing
        │                 │                 │
        ▼                 ▼                 ▼
   OTP / Session      AI Usage          Razorpay
        │                 │                 │
        ▼                 ▼                 ▼
  User / Profile     Credit Engine      Payment
                          │                 │
                          ▼                 ▼
                    Entitlement       Subscription
                          │
                          ▼
                       Database
```

Business logic is not scattered across views — it lives in a dedicated **service layer**:

```
accounts/services/
│
├── credit_service.py
├── entitlement_service.py
├── entitlement_admin.py
├── subscription_service.py
├── payment_service.py
├── razorpay_client.py
├── ai_usage_service.py
├── guest_identity.py
└── account_deletion_service.py
```

## 📂 Project Structure

```
AI_GENERATORS/
│
├── accounts/
│   ├── services/        # Business logic (credits, entitlements, billing, deletion)
│   ├── tests/            # 100 test files, 838 tests
│   └── ...
├── templates/            # HTML templates
├── staticfiles/           # Static assets
├── manage.py
└── requirements.txt
```

## ✅ Verification Status

Overall: **~95–98% feature-complete and production-hardened.** The honest claim is *not* "100% production-verified" — two items are environment-dependent and remain open.

| Area | Status |
|---|---|
| Django backend, auth, credit engine, entitlements | ✅ Complete |
| 5 AI generators + shared lifecycle, AI history | ✅ Complete |
| Razorpay checkout, webhook & refund logic (code-level) | ✅ Implemented |
| Redis rate limiting, CSRF/XSS/IDOR audit | ✅ Audited |
| Account deletion & financial-retention hardening | ✅ Complete |
| Automated tests | ✅ 838/838 passing, 9 skipped |
| `manage.py check` / migration drift | ✅ 0 issues / none |
| PostgreSQL row-locking under real concurrency | ⚠️ Not independently re-verified — existing concurrency tests ran on SQLite |
| Live Razorpay TEST-mode E2E | ⚠️ Not re-run in the current environment (no live credentials available) |
| Content-Security-Policy (CSP) | ⚠️ Not yet implemented |
| Dedicated load/performance benchmark | ⚠️ Not performed |

**Recommended framing for reports/presentations:**

> "The application is feature-complete and production-hardened across nine development phases, with 838 automated tests passing. Remaining verification items are environment-dependent PostgreSQL concurrency and live Razorpay sandbox E2E validation."

## 📈 Future Enhancements

- PostgreSQL production deployment with re-verified concurrency testing
- Automated CI/CD pipeline
- Content-Security-Policy (CSP)
- Comprehensive load/performance testing
- Real-time streaming AI responses
- Advanced per-plan usage analytics dashboard

## 🔐 OTP & Email Verification

This project sends OTP/transactional email via the Brevo transactional email API (see `EMAIL_SETUP.md` for full setup). Brevo requires verifying a **sender email address** (not a full DNS domain) before it will deliver to real recipients — set `BREVO_API_KEY`, `EMAIL_FROM`, and `EMAIL_FROM_NAME` in your environment once that's done. Until a sender is verified, OTP emails will fail to send and signup/password-reset will surface a clear "couldn't send verification code" error rather than silently failing.

💡 See `EMAIL_SETUP.md` for the full Brevo setup walkthrough, rate-limiting behavior, and common error troubleshooting.

## 👨‍💻 Author

**Vivek Kumar**
B.Tech Computer Science Student, GLA University, Mathura

## ⭐ Support

If you like this project, give it a ⭐ on GitHub and connect with me on LinkedIn.