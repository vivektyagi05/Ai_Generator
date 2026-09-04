# PHASE 8A SYSTEM ARCHITECTURE

Describes the actual implementation as it exists after Items 1-9, read
from the real source (not assumed from PHASE_7_ARCHITECTURE.md's
description of the pre-8A state, though it's still accurate for
anything this phase didn't touch — generator controller/state machine,
CSS token system, credit ledger internals are unchanged and that doc
remains the reference for them).

## Identity → entitlement → credit → provider chain

```
AUTH        request.user (session) OR guest_identity.identify_requester()
  │
  ▼
SESSION     Django session (guest_key = session key until a real user exists)
  │
  ▼
ENTITLEMENT entitlement_service.get_access_decision(user, guest_key, feature)
  │           - authenticated: plan-based limits (accounts/entitlement_config.py)
  │           - guest: ALWAYS AUTHENTICATION_REQUIRED (Phase 8A -- see
  │             PHASE_8A_BUSINESS_RULES.md)
  ▼
CREDIT      credit_service.get_or_create_account() -- never reached for
ACCOUNT     a guest anymore, since entitlement denies first
  │
  ▼
AI REQUEST  ai_usage_service.begin_request() -> credit_service.reserve()
  │           (select_for_update + DB unique constraint on
  │            (account, reference_id=request_id, transaction_type))
  ▼
PROVIDER    AI_GENERATORS/api_views.py -- requests.post to Groq (text)
  │           or the image provider (Photo)
  ▼
SUCCESS -> ai_usage_service.complete_success() -> credit_service.finalize()
FAILURE -> ai_usage_service.complete_failure() -> credit_service.refund()
  │
  ▼
HISTORY     ChatHistory (save_history / auto-saved on generator success)
  │
  ▼
BILLING     accounts/billing_views.py + static/js/billing.js
            (pre-existing from an earlier phase; Phase 8A only
            re-verified the UI is wired to it -- see Item 9 in
            PHASE_8A_PROGRESS.md; the remaining Razorpay/checkout work
            itself is explicitly Phase 8B's, not touched here)
```

## Navigation (Phase 8A fix)

Template inheritance was already single-source before this phase:

```
base_public.html --includes--> partials/nav_public.html
base_app.html    --includes--> partials/nav_app.html

login/signup/forget/verify_otp.html --extends--> base_public.html
dashboard/history/profile/plans/main.html --extends--> base_app.html
```

No page extends or includes more than one nav. The reported "duplicate
nav" was not a template-architecture bug at all — it was
`static/css/shell.css` failing to hide `nav_app.html`'s mobile drawer
(`.ds-nav-mobile-panel`) by default outside its `@media (max-width:
768px)` block. See PHASE_8A_PROGRESS.md Item 1 for the full root-cause
trace. One-line CSS fix, template tree unchanged.

## CSS dependency graph (Item 8 — verified clean, unchanged)

```
tokens.css  →  components.css  →  shell.css   (base_public.html, base_app.html)
                                →  billing.css (plans.html, profile.html only)

+ per-page <style> blocks (dashboard/forget/history/main/profile/verify_otp.html)
  -- verified: no class name collision with components.css/shell.css
```

## Generator controller (unchanged from Phase 7 — see PHASE_7_ARCHITECTURE.md)

All 5 generators still call the same `runGeneratorLifecycle()` in
`templates/main.html`. Phase 8A's Item 3 re-verified this end-to-end
for Story/Application/Proposal/Code (Photo already covered by
`test_ai_photo_integration.py`) and found no divergence — see
PHASE_8A_GENERATOR_CONTRACT.md for the per-generator matrix.

## Auth security primitives already in place (verified this phase, not built this phase)

- Session-fixation: `accounts/views.py:user_login()` reads guest
  identity *before* calling Django's `login()`, which itself rotates
  the session key (`cycle_key()`).
- Open redirect: `user_login()`'s `next` param is validated via
  `url_has_allowed_host_and_scheme()` before use.
- File upload (avatar): `accounts/views.py:profile_avatar()` enforces
  a size cap, a Content-Type pre-filter, and a real Pillow
  decode-and-verify of file content (not just trusting metadata).
- XSS: every AI-generated result rendered into `innerHTML` anywhere in
  `templates/main.html` goes through `escapeHtml()` first (checked
  across every generator's `onSuccess` handler).
- Secrets: no API key, Razorpay secret, or credential found hardcoded
  in any template or static JS file (checked by pattern search); only
  the Razorpay public `key_id` (server-issued per order) reaches the
  client, in `static/js/billing.js`.

## Files touched this phase (Items 1-9)

`static/css/shell.css`, `accounts/services/entitlement_service.py`,
`AI_GENERATORS/api_views.py`, `accounts/otp_service.py`,
`templates/signup.html`, plus the test files listed per-item in
PHASE_8A_PROGRESS.md. No new app, no new template, no new CSS/JS
framework, no duplicate service — every fix landed inside the existing
file that already owned the responsibility.
