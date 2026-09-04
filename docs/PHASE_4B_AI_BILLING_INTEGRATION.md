# PHASE 4B — AI Billing Integration (Closure)

## Original gap

The first PHASE 4B pass's own Completion Audit flagged: "The
AI-generation credit-exhaustion UI ... was not implemented ...
`main.html`'s existing error handling was not extended with an upgrade
CTA." This document covers closing exactly that gap.

## Step 1 forensic trace (main.html → /api/ai/ → backend)

Traced the real path, not assumed:

```
templates/main.html: 4 generator functions (generateCode/generateStory/
generateApplication/generateProposal), each calling the shared
aiRequest(prompt, {feature, requestId}) helper
  -> POST /api/ai/ (AI_GENERATORS/api_views.py:gemini_api)
    -> CSRF (Django middleware) -> guest_identity resolution
    -> _check_access_policy() -> entitlement_service.get_access_decision()
       -- denies with 401/403/429, body: {"success": false,
          "error": {"code": "...", "message": "..."}}
    -> rate_limit.check_ai_rate_limit() -- 429 on rate limit (separate
       from quota; body shape not re-audited, out of this phase's scope)
    -> ai_usage_service.begin_request() -> credit_service.reserve()
       -- denies with InsufficientCreditsError -> 402, body:
          {"error": "Insufficient credits for this request.",
           "request_id": "..."}
    -> _call_provider() (Groq) -> 200 on success, 502/503 on provider
       failure, all with {"error": "<plain string>", "request_id": ...}
```

Confirmed via direct code read (`AI_GENERATORS/api_views.py`) and via
live requests through Django's test client (not assumed):

| Status | Trigger | Body shape |
|---|---|---|
| 200 | success | `{"result", "request_id", "credits_charged", "credits_available"}` |
| 400 | validation/bad feature | `{"error": "<string>", "request_id"}` |
| 401 | `AUTHENTICATION_REQUIRED` | `{"success": false, "error": {"code", "message"}}` |
| 402 | `InsufficientCreditsError` (credit ledger empty) | `{"error": "<string>", "request_id"}` |
| 403 | `FEATURE_DISABLED` / `PLAN_NOT_ALLOWED` / `ENTITLEMENT_*` / `GUEST_RESTRICTION` | `{"success": false, "error": {"code", "message"}}` |
| 429 | `QUOTA_EXCEEDED` (entitlement daily/monthly cap) | `{"success": false, "error": {"code", "message"}}` |
| 502/503 | provider failure/timeout | `{"error": "<string>", "request_id"}` |

Two genuinely different response shapes exist for errors (plain-string
`error` vs. `{code, message}` object `error`), depending on which layer
rejected the request. This was not documented anywhere before this pass
and is the root cause of the bug described next.

## Root cause of the actual gap (found, not assumed)

Two separate, real problems, both confirmed by reading the code (not
guessed):

1. **No frontend code reacted to 402/403(PLAN_NOT_ALLOWED)/429
   differently from any other failure.** All four generators funneled
   every non-2xx response through one line:
   `escapeHtml(result.error)` displayed as plain red text — including
   credit exhaustion, which deserved a real upgrade path.
2. **`escapeHtml()` was declared *inside* `generateCode()` only**
   (`templates/main.html`, pre-closure line ~1533). Because of JavaScript
   function-scoping rules, that declaration is invisible to
   `generateStory()`, `generateApplication()`, and `generateProposal()` —
   confirmed by checking each function's enclosing braces. Any failure in
   those three generators (network error, credit exhaustion, provider
   failure — anything) called an undefined function and threw
   `ReferenceError: escapeHtml is not defined`, which **silently broke
   the whole error path** for 3 of the 4 generators — no message shown
   at all, not even the pre-existing generic one. This existed before
   this phase and was unrelated to billing specifically, but it directly
   blocked building working failure states (Step 6), so it had to be
   fixed as part of this closure.

## Implementation

- `escapeHtml()` moved to top-level script scope (shared by all 4
  generators); the duplicate nested copy inside `generateCode()` removed.
- New shared functions (`templates/main.html`, all top-level, no
  duplication across the 4 generators):
  - `extractApiError(data, fallback)` — normalizes both response shapes
    from the table above into one `{message, code}`.
  - `isExhaustionError(status, code)` — `true` for 402 (credit ledger
    empty), 429+`QUOTA_EXCEEDED` (daily/monthly cap), or
    403+`PLAN_NOT_ALLOWED` (feature gated to a higher plan). Everything
    else (network/provider/validation) stays a normal error.
  - `renderAiFailure(outputEl, result)` — the single dispatch point all 4
    generators now call from their `if (!result.ok)` branch. Routes to
    `renderSignInRequired` (401), `renderExhaustionCard` (exhaustion), or
    the existing plain red-text error (everything else).
  - `renderExhaustionCard(outputEl, message)` — the real UI from Step 3:
    "Credits exhausted" title, the server's own message, and a
    **functional** CTA. For a signed-in user: a real button wired to
    `window.BillingFlow` (see below). For a guest: a "Sign in to
    upgrade" link (purchasing requires an account — `create-order` is
    `@login_required` — so a guest is never shown a purchase button that
    can't work).
  - `onExhaustionUpgradeSuccess(card, outputEl)` — Step 5/9: after
    `BillingFlow` reaches `SUCCESS` (which, per `static/js/billing.js`,
    only happens after the server confirms `Payment=CAPTURED` **and**
    `Subscription=ACTIVE`), this calls the existing
    `GET /api/entitlements/` to re-derive access from the server, then
    replaces the exhaustion card with a confirmation. It does not set
    any local "premium" flag — the user's next click of the page's own
    Generate button re-runs `aiRequest()` for real and gets a genuine
    server decision.

## Frontend↔billing connection (Step 4 — one flow, not a duplicate)

`renderExhaustionCard`'s CTA calls
`window.BillingFlow.create({...}).upgrade({plan: "PREMIUM", ...})` —
the exact same `window.BillingFlow` object `templates/plans.html` and
`static/js/profile-billing.js` use, loaded via one
`<script src="{% static 'js/billing.js' %}">` tag added to
`templates/main.html`'s `<head>`. No second `create-order`/
`verify-payment`/polling implementation exists anywhere in this
codebase — confirmed by the forensic sweep in
`PHASE_4B_COMPLETION_CLOSURE.md`.

## What main.html was NOT changed to do

- No credits/usage widget was added to `main.html` — it never had one,
  and adding a persistent one was outside the actual gap that was
  flagged (the exhaustion moment itself, not a standing dashboard;
  that already exists on `/profile/`).
- The pre-existing `downloadGeneratedCode()`/`copyGeneratedCode()`/
  `resetCodeGenerator()` scoping (declared inside `generateCode()`, but
  invoked via `onclick="..."` attributes at global scope — see the
  buttons around `templates/main.html` line 926) has the **same kind**
  of bug `escapeHtml()` had, but it is unrelated to billing/credits and
  was left alone to avoid unrelated scope creep in a billing-focused
  pass. Flagged here explicitly, not hidden.
