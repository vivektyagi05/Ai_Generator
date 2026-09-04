# Architecture

This document describes the architecture found in the current AI Generator project. It is intentionally aligned with the existing implementation rather than introducing a new application structure.

## Application boundary

`AI_GENERATORS/` is the Django project package. It owns settings, URL routing, WSGI/ASGI entry points, page-level views and the shared AI API entry point.

`accounts/` owns the user/account domain plus the credit, entitlement, subscription, payment, OTP/email, history and profile functionality.

## Service layer

The canonical domain services are:

| Service | Responsibility |
|---|---|
| `credit_service.py` | credit balance/ledger reservation, finalization and refunds |
| `entitlement_service.py` | feature access and plan entitlement decisions |
| `entitlement_admin.py` | administrative entitlement operations |
| `subscription_service.py` | subscription state transitions and lifecycle rules |
| `payment_service.py` | payment/order/webhook orchestration |
| `razorpay_client.py` | single integration boundary for Razorpay SDK/secrets |
| `ai_usage_service.py` | shared AI usage lifecycle and usage accounting |
| `guest_identity.py` | guest identity support for non-AI/general infrastructure paths |
| `account_deletion_service.py` | anonymization, session revocation and retention-safe deletion |

## AI request lifecycle

```text
HTTP request
  ↓
request validation
  ↓
authentication / authorization
  ↓
rate-limit decision
  ↓
entitlement decision
  ↓
credit reservation
  ↓
provider call
  ├── success → finalize actual charge → save usage/history
  └── failure → refund reservation → return structured failure
```

Unauthenticated users must not reach the provider or credit ledger for AI generation.

## Billing lifecycle

```text
Authenticated user
  ↓
server resolves plan + interval + amount
  ↓
local Payment record
  ↓
Razorpay Order
  ↓
checkout
  ↓
server verifies checkout callback signature
  ↓
Razorpay webhook signature verification
  ↓
Payment state
  ↓
Subscription lifecycle
  ↓
Entitlement/access state
```

The browser is not the source of truth for payment capture or subscription activation.

## Data integrity rules

- Credit mutations are transactional.
- Idempotency is enforced at the database/service boundary where the operation requires it.
- Shared mutable rows use `select_for_update()` where supported/appropriate.
- Financial and usage records are retained according to the account-deletion policy rather than destructively cascaded.
- Migrations are the source-controlled representation of schema changes.

## Configuration boundaries

Environment configuration is loaded by `AI_GENERATORS/settings.py` and the email/configuration modules. Razorpay secrets are intentionally centralized in `accounts/services/razorpay_client.py`.

The source `static/` directory is the canonical frontend asset tree. `collectstatic` output belongs in `staticfiles/` and is not source-controlled.
