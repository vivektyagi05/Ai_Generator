# Security Policy

AI Generator handles authentication, credit/billing data, and payment webhooks (Razorpay), so security reports are taken seriously and triaged ahead of feature work.

## Supported Versions

| Version / Branch | Supported |
|---|---|
| `main` (Phase 9 and later) | ✅ Yes |
| Pre-Phase-9 snapshots / archived ZIPs | ❌ No |

Only the current `main` branch receives security fixes. Pre-Phase-9 builds predate login throttling, the hardened account-deletion flow, and the Pillow upload fix, and should not be run in production.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report privately:

- Email: **security@[your-domain-here]** (replace with your monitored address before publishing this file)
- Alternatively, use GitHub's private **"Report a vulnerability"** flow under the repository's Security tab, if enabled

Please include:

1. A clear description of the vulnerability and its impact
2. Steps to reproduce (a minimal repro is ideal)
3. The affected endpoint(s), module(s), or file(s), if known
4. Whether the issue is already public or has been shared elsewhere

### What to expect

| Stage | Target time |
|---|---|
| Acknowledgement of your report | Within 3 business days |
| Initial triage / severity assessment | Within 7 business days |
| Fix or mitigation for confirmed critical issues | Best-effort, prioritized over feature work |
| Public disclosure | Coordinated with the reporter, after a fix ships |

We ask reporters not to publicly disclose a vulnerability until a fix has been released or 90 days have passed, whichever comes first.

## Scope

**In scope:**
- Authentication and session handling (signup, login, OTP, password reset)
- The credit engine (reserve/finalize/refund logic, idempotency, concurrency)
- Entitlement and subscription access control
- Razorpay integration (order creation, webhook signature verification, refund handling)
- Account deletion / data-retention logic
- File upload handling (avatars)
- CSRF/XSS/IDOR issues in any view or API endpoint

**Out of scope:**
- Vulnerabilities in third-party services themselves (Razorpay, Groq, Pollinations, Brevo, Redis, Render) — please report those directly to the respective vendor
- Denial-of-service reports that require unrealistic traffic volume with no underlying logic flaw
- Missing security headers with no demonstrated exploit (e.g. a CSP report with no working XSS payload) — these are welcome as regular issues, just not as urgent security reports
- Social engineering, physical attacks, or attacks on infrastructure the maintainer doesn't control

## What This Project Already Does

To help you focus a report on something new rather than a known/accepted design decision, here is the current security posture:

- **Authentication:** OTP-verified signup and password reset, PBKDF2-hashed passwords, session-based auth with secure/HttpOnly cookies, and IP- **and** email-based login throttling (nonexistent emails are throttled identically to real ones, so login protection cannot be used to enumerate accounts).
- **CSRF / XSS / IDOR:** audited across all state-changing routes; Django's CSRF middleware is enforced, and the frontend's `api.js` centralizes CSRF-token attachment.
- **Payment security:** Razorpay webhooks are verified against their raw request body via HMAC signature before any state change is applied. A client-side "payment success" callback is never trusted on its own.
- **Financial integrity:** every credit movement is an immutable `CreditTransaction` row; `transaction.atomic()` and `select_for_update()` row locking guard against concurrent double-spend, and a unique `(account, reference_id, transaction_type)` constraint makes retried requests idempotent.
- **Account deletion:** anonymizes personal data, revokes sessions, and cancels active subscriptions, while preserving `Payment`, `CreditTransaction`, `AIUsage`, and `Subscription` records for audit purposes — deletion never cascades destructively.
- **Upload handling:** avatar uploads are validated against malformed/malicious images, including Pillow's `DecompressionBombError`.
- **Rate limiting:** Redis-backed in production (`django-redis`), with a documented `LocMemCache` fallback for local development.
- **Secrets management:** all API keys and `SECRET_KEY` are environment-variable-based; nothing is hardcoded in source.

## Known, Tracked Gaps (Not a Vulnerability Report Needed)

These are already tracked as open work — no need to report them, but pull requests are welcome:

- **Content-Security-Policy (CSP) is not yet implemented.**
- PostgreSQL-specific concurrency behavior has not been independently re-verified outside of SQLite-based tests.
- No dedicated load/performance benchmark has been run against the rate-limiting configuration.

## Safe Harbor

Good-faith security research that stays within this scope, avoids privacy violations, service degradation, or data destruction, and gives us a reasonable opportunity to fix the issue before public disclosure will not result in legal action from this project.