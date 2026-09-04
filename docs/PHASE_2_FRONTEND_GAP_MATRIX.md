# PHASE 2 — Frontend/Backend Contract Matrix + Gap Classification

## Contract matrix

Endpoint → method → auth → frontend caller. Traced from `accounts/urls.py`
and `AI_GENERATORS/urls.py` against every `fetch(`/form `action=` in
`templates/`.

### AUTH
| Endpoint | Method | Auth | CSRF | Frontend caller |
|---|---|---|---|---|
| `/signup/` | GET/POST | none | yes (POST) | `signup.html` |
| `/verify-otp/` | GET/POST | none | yes (POST) | `verify_otp.html` |
| `/resend-otp/` | POST | none | yes | `verify_otp.html` |
| `/login/` | GET/POST | none | yes (POST) | `login.html` |
| `/logout/` | GET | authenticated | n/a | nav links in `main.html`/`profile.html` |
| `/forgot/` | GET | none | n/a | `forget.html` |
| `/forgot/send-otp/` | POST | none | yes | `forget.html` |
| `/forgot/resend-otp/` | POST | none | yes | `forget.html` |
| `/forgot/verify-otp/` | POST | none | yes | `forget.html` |
| `/forgot/reset-password/` | POST | none | yes | `forget.html` |

### GENERATORS
| Endpoint | Method | Auth | Frontend caller | feature value |
|---|---|---|---|---|
| `/api/ai/` | POST | guest or authenticated (policy-checked server-side) | `main.html` `aiRequest()`, 5 call sites | `photo`, `story`, `application`, `proposal`, `code` |

All 5 generators share one endpoint and one client helper — see
`PHASE_2_STEP_1_FRONTEND_FORENSIC_AUDIT.md` §2. States handled by
`aiRequest()`/its callers: loading (`btn.disabled = true` + spinner text),
success (`result` rendered into the tab), error (generic), 402
credit-exhausted (dedicated modal, `main.html` ~line 1330), 401
auth-required (dedicated prompt). No empty state applies (generators don't
list anything).

### ACCOUNT
| Endpoint | Method | Frontend caller |
|---|---|---|
| `/profile/` | GET | direct nav |
| `/profile/data/` | GET | `profile.html` (loads current values) |
| `/profile/update/` | POST | `profile.html` |
| `/profile/change-password/` | POST | `profile.html` |
| `/profile/delete/` | POST | `profile.html` |
| `/profile/avatar/` | POST | `profile.html` |

Loading/success/error states all present and served from real data —
`email.disabled = true` (line 1054) because email genuinely isn't editable,
not a stub.

### HISTORY
| Endpoint | Method | Frontend caller |
|---|---|---|
| `/history/` | GET | direct nav, `history.html` |
| `/history/delete/<id>/` | POST | `history.html` |
| `/history/clear/` | POST | `history.html` |
| `/save_history/` | POST | `main.html` (after each successful generation) |

No search/filter or pagination endpoint exists, and none is referenced —
not a gap, `history.html` doesn't claim either capability.

### CREDITS
| Endpoint | Method | Frontend caller |
|---|---|---|
| `/credits/balance/` | GET | **none** — see GAP-04 below |

### BILLING
| Endpoint | Method | Frontend caller |
|---|---|---|
| `/api/plans/`, `/plans/` | GET | `plans.html` |
| `/api/entitlements/` | GET | `main.html` (line 1449), `profile.html` |
| `/api/subscription/` | GET | `profile.html` via `profile-billing.js` |
| `/api/subscription/cancel/`, `/restore/` | POST | `profile.html` |
| `/api/billing/create-order/` | POST | `plans.html`/`profile.html` via `billing.js` |
| `/api/billing/verify-payment/` | POST | `billing.js` (after Razorpay checkout) |
| `/api/billing/payment-status/<order_id>/` | GET | `billing.js` (polling fallback) |
| `/api/webhooks/razorpay/` | POST | Razorpay server → server; no frontend caller (correct — see `accounts/webhook_views.py` docstring) |

Every backend endpoint that should have a frontend caller has one. The one
callable endpoint with **no** caller anywhere is `/credits/balance/` — see
GAP-04 below, promoted from the forensic audit into this matrix because it's
the one true "backend capability exists, frontend doesn't expose it" gap in
the whole matrix.

## Gap classification (carried from the forensic audit, P0–P3)

| ID | Finding | Class | Type |
|---|---|---|---|
| GAP-01 | No shared template/CSS/JS layer; 8 independent stylesheets/scripts | P1 | Architecture |
| GAP-02 | CSRF/fetch/error logic duplicated 6+ times | P2 | Architecture |
| GAP-03 | 4 incompatible visual languages across 8 pages | P1 | UX/consistency |
| GAP-04 | `/credits/balance/` has no frontend caller — balance invisible outside `/profile/` | P1 | Missing exposure (real gap — backend capability, no frontend surface) |
| GAP-05 | No deep-link/cross-page path to a specific generator | P2 | UX/navigation |
| GAP-06 | Loading/error/toast UI hand-rolled independently on 6 pages | P2 | Architecture |
| GAP-07 | Guest nav mixes raw `href` and `{% url %}` inconsistently | P3 | Polish |

No P0 findings — no security/correctness problem was found in the frontend.
