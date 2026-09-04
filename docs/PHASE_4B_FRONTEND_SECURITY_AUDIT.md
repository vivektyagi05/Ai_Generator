# PHASE 4B — Frontend Security Audit (Closure Pass)

Scope: everything added/changed in this closure pass —
`templates/main.html`'s new exhaustion-UI functions and script includes,
`static/css/billing.css`'s new `.ai-exhaustion-card` rules. The Phase 4
backend, `static/js/billing.js`, and `static/js/profile-billing.js` were
**not modified** this pass; their properties (audited in
`PHASE_4B_SECURITY_AUDIT.md`) are unchanged and re-confirmed still true
by the full test suite passing.

## Client-controlled values

`renderExhaustionCard`'s upgrade button calls
`flow.upgrade({plan: "PREMIUM", billingInterval: "MONTHLY"})` — identical
literal call already used by `plans.html`/`profile-billing.js`. No value
here originates from the AI response body (`result.data`) or from
anything else client-supplied; `PREMIUM`/`MONTHLY` are fixed strings, and
`billing.js`'s `create-order` call still lets the backend resolve the
real price regardless (see the original security audit — unchanged).

## XSS

Every new function in this pass (`renderSignInRequired`,
`renderExhaustionCard`, `onExhaustionUpgradeSuccess`) builds DOM
exclusively with `document.createElement` + `.textContent` for every
value that could contain server- or attacker-influenced text (the
`message` string from the AI API's error body). Verified with:

```
grep -n "textContent\|innerHTML" <the new code range>
```

— every `innerHTML` use in the new code is either `= ""` (clearing) or
the one pre-existing plain-error fallback path
(`renderAiFailure`'s final line), which still routes through
`escapeHtml()` exactly as the original code did.

## The `escapeHtml` fix itself

Moving `escapeHtml` to top-level scope doesn't change its
implementation (same three `.replace()` calls, now additionally
`String(text)`-coerced so a non-string `message` can't throw instead of
rendering) — it only fixes *reachability*. This closes a real
availability gap (3 of 4 generators previously threw on any failure
instead of showing anything), not a new security surface.

## Secrets

`grep -rn "RAZORPAY_KEY_SECRET\|RAZORPAY_WEBHOOK_SECRET" templates/main.html static/`
→ no matches (re-run this pass, same result as the original audit).

## CSRF

The exhaustion CTA's purchase flow goes entirely through
`window.BillingFlow`, which already attaches `X-CSRFToken` to its own
`create-order`/`verify-payment` calls (unchanged this pass — see the
original security audit). `onExhaustionUpgradeSuccess`'s
`GET /api/entitlements/` call is a read, no CSRF token needed (Django
doesn't require one for GET).

## Duplicate-request protection

`renderExhaustionCard` creates exactly one `BillingFlow` instance per
render of the card, and that instance's own `inFlight` guard (unchanged,
in `billing.js`) blocks a second `upgrade()` call while one is running —
same protection `plans.html`/`profile.html` rely on, not a separate
implementation. The button is disabled synchronously in the same click
handler that starts the request (`btn.disabled = true;` before
`flow.upgrade(...)`), so there's no window where a second click could
fire before the guard is set.

## No client-side premium assertion

`grep -rn "isPremium\s*=\s*true\|is_premium\s*=\s*true\|premium\s*=\s*true"
templates/main.html static/js/*.js` → no matches. Access after a
successful upgrade is re-derived via `GET /api/entitlements/`, and actual
generation availability is re-checked for real on the user's next
`aiRequest()` call — nothing here ever assumes success client-side.

## Known limitation carried over

Same as the original security/completion audits: real Razorpay
TEST-mode and browser click-through remain unverified in this sandbox
(no network route to Razorpay, no browser automation). This pass didn't
change that constraint.
