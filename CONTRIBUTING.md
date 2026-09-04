# Contributing to AI Generator

Thanks for your interest in contributing. This project is a production-hardened Django AI SaaS platform (credit engine, entitlements, Razorpay billing, OTP auth), so contributions that touch billing, credits, or authentication are held to a higher bar than a typical UI tweak. This guide explains how to get set up, how the codebase is organized, and what's expected in a pull request.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Project Architecture](#project-architecture)
4. [Development Workflow](#development-workflow)
5. [Coding Standards](#coding-standards)
6. [Testing Requirements](#testing-requirements)
7. [Commit & Branch Conventions](#commit--branch-conventions)
8. [Pull Request Process](#pull-request-process)
9. [Reporting Bugs & Requesting Features](#reporting-bugs--requesting-features)
10. [Security Issues](#security-issues)

## Code of Conduct

Be respectful, assume good intent, and keep discussion focused on the technical merits of a change. Disagreements about implementation are fine; personal attacks are not.

## Getting Started

### Prerequisites

- Python 3.10+
- pip / virtualenv
- Redis (optional for local dev — the project falls back to `LocMemCache` automatically if `REDIS_URL` is not set)
- A Razorpay **test-mode** account if you're working on billing (never use live keys in development)
- A Brevo account (or equivalent) if you're working on OTP/email flows

### Setup

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/AI_GENERATORS.git
cd AI_GENERATORS

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in environment variables
cp .env.example .env
# Fill in: SECRET_KEY, BREVO_API_KEY, EMAIL_FROM, EMAIL_FROM_NAME,
#          RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET,
#          REDIS_URL (optional locally)

# 5. Run migrations
python manage.py migrate

# 6. Run the test suite before you change anything, to confirm a clean baseline
python manage.py test accounts

# 7. Start the dev server
python manage.py runserver
```

If `manage.py test` doesn't pass cleanly on a fresh clone, please open an issue before starting work — that's a bug in its own right.

## Project Architecture

Business logic lives in the **service layer**, not in views or templates. Before adding a new feature, find (or create) the relevant service:

```
accounts/services/
│
├── credit_service.py            # Credit reserve / finalize / refund
├── entitlement_service.py       # Plan → Feature → access resolution
├── entitlement_admin.py         # Admin-side entitlement management
├── subscription_service.py      # Subscription state machine
├── payment_service.py           # Razorpay order/payment orchestration
├── razorpay_client.py           # Thin Razorpay API wrapper
├── ai_usage_service.py          # Shared AI generator lifecycle
├── guest_identity.py            # Anonymous/guest request handling
└── account_deletion_service.py  # Anonymize-and-preserve deletion flow
```

**Rules of thumb:**

- A Django **view** should orchestrate a request/response — validate input, call a service, render/return a result. It should not itself mutate credits, payments, or subscriptions directly.
- Anything that touches `CreditTransaction`, `Payment`, or `Subscription` must run inside `transaction.atomic()`, and any read-then-write on a shared row (e.g. a credit balance) must use `select_for_update()`.
- New AI generator types should be added by extending the shared lifecycle in `ai_usage_service.py` (validate → authorize → reserve → call provider → finalize/refund → save history) rather than writing a new one-off controller.
- Razorpay webhook handling must verify the HMAC signature against the **raw request body** before touching any model state. Never trust a client-reported payment status.
- Account-deletion changes must preserve `Payment`, `CreditTransaction`, `AIUsage`, and `Subscription` records — anonymize, don't cascade-delete.

## Development Workflow

1. **Open an issue first** for anything non-trivial (new feature, architectural change, or anything touching credits/billing/auth), so the approach can be discussed before code is written.
2. **Create a branch** off `main` (see naming convention below).
3. **Write the code**, following the architecture rules above.
4. **Write or update tests** — see [Testing Requirements](#testing-requirements).
5. **Run the full suite locally** before opening a PR.
6. **Open a pull request** against `main` with a clear description (see PR template below).

## Coding Standards

- Follow [PEP 8](https://peps.python.org/pep-0008/); run `black` and `isort` (or your configured formatter/linter) before committing if the repo has one configured.
- Prefer explicit, descriptive names over abbreviations — this is financial/billing code; clarity beats brevity.
- Docstring any service-layer function whose behavior isn't obvious from its name, especially anything involving credit math or state transitions.
- Avoid silent `except:` blocks. If you catch an exception, either handle it meaningfully or re-raise with added context. Payment and webhook code in particular must never fail silently.
- New database fields require a migration in the same PR — do not hand-edit migration files.
- Frontend JS should go through `api.js` for fetch/CSRF/error handling rather than reimplementing it per page.

## Testing Requirements

This project currently ships with **838 passing tests across 100 test files** (9 skipped) as its production-readiness baseline. Contributions are expected to keep that number moving in the right direction, not the wrong one.

- **Any behavior change requires a test.** Bug fixes should include a regression test that fails before the fix and passes after.
- **Credit, entitlement, subscription, and payment logic require tests for both the happy path and the failure path** (e.g. an AI provider failure must be tested for its refund behavior, not just its success behavior).
- **Concurrency-sensitive code** (credit reservation, webhook processing) should include a test that simulates duplicate/concurrent requests where practical.
- Run the full suite before opening a PR:

```bash
python manage.py test accounts
python manage.py check
python manage.py makemigrations --check --dry-run
```

A PR that reduces the passing-test count, introduces migration drift, or fails `manage.py check` will not be merged until that's resolved.

## Commit & Branch Conventions

**Branch naming:**

```
feature/<short-description>     # new functionality
fix/<short-description>         # bug fix
hardening/<short-description>   # security/production-hardening work
docs/<short-description>        # documentation only
```

**Commit messages** — follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(credit-engine): add partial-refund support for failed multi-step generations
fix(auth): prevent login-throttle bypass via case-varied email
docs(readme): correct Django version and remove stale roadmap items
hardening(uploads): reject nested zip-bomb style image payloads
```

## Pull Request Process

1. Fill out the PR description: **what** changed, **why**, and **how it was tested**.
2. Link the related issue, if any.
3. Confirm in the PR description that:
   - [ ] `python manage.py test accounts` passes locally
   - [ ] `python manage.py check` reports 0 issues
   - [ ] No migration drift (`makemigrations --check --dry-run` is clean)
   - [ ] New/changed behavior has test coverage
   - [ ] Credit/payment/auth changes were tested for both success and failure paths
4. A maintainer will review for correctness, architectural fit (service-layer placement, transaction safety), and test coverage before merging.
5. PRs touching billing, credits, or account deletion should expect closer review and may take longer to merge — this is by design, not a reflection on the contribution.

## Reporting Bugs & Requesting Features

Use GitHub Issues for both. For bugs, please include:

- Steps to reproduce
- Expected vs. actual behavior
- Django version, Python version, and whether you're using SQLite or PostgreSQL
- Relevant logs or stack traces (redact any secrets, tokens, or personal data first)

For feature requests, briefly describe the use case, not just the desired implementation — this helps evaluate whether it fits the existing service-layer architecture or needs a new one.

## Security Issues

**Do not open a public issue for a security vulnerability.** See [SECURITY.md](./SECURITY.md) for the private reporting process.

---

Thank you for contributing — well-tested, architecturally consistent contributions are what keep this project's "838/838 tests passing" claim meaningful rather than decorative.