# PHASE 5 — AI Generator Forensic Findings (Step 0 / Step 1)

Status legend: VERIFIED (read the exact code) · CODE-VERIFIED (traced through
call sites) · MISSING · BROKEN · NOT VERIFIED (not yet inspected this pass)

## 1. Backend contract — single unified endpoint

`POST /api/ai/` → `AI_GENERATORS/api_views.py:ai_generate()` — **VERIFIED**,
one view handles all five generators via a `feature` field:
`chat | code | story | application | proposal | photo`
(`accounts/ai_request_config.py: ALLOWED_FEATURES`).

Lifecycle, in order — **VERIFIED** by reading the view top to bottom:
method check → CSRF (middleware) → identity (`guest_identity`) →
content-type/size/JSON parse → `feature` resolved → entitlement/quota
(`entitlement_service.get_access_decision`) → rate limit
(`rate_limit.check_ai_rate_limit`) → prompt-length validation →
idempotency replay (`ai_usage_service.check_existing_usage`) → credit
reservation (`ai_usage_service.begin_request` → `credit_service.reserve`) →
provider call → finalize/refund → quota usage record → response.

Response contract — **VERIFIED**: success →
`{result, request_id, credits_charged, credits_available}` (photo's
`result` is actually an image URL, not text — see §4). Failure →
`{error}` or `{error:{code,message}}` with status ∈
`400,401,402,403,405,409,413,429,500,502,503`.

## 2. Feature contract matrix

| Generator | Backend feature slug | Credit model | Entitlement registered | Frontend function | Uses shared `DS.api`/idempotency helper | History tagged by feature |
|---|---|---|---|---|---|---|
| Story | `story` | token-based (`credit_config.tokens_to_credits`) | VERIFIED (`entitlement_config.FEATURE_DEFINITIONS`) | `generateStory()` (main.html:1907) | uses local `aiRequest()`, not `DS.api` | **NO — see §5** |
| Application | `application` | token-based | VERIFIED | `generateApplication()` (main.html:2049) | local `aiRequest()` | **NO** |
| Proposal | `proposal` | token-based | VERIFIED | `generateProposal()` (main.html:2241) | local `aiRequest()` | **NO** |
| Code | `code` | token-based | VERIFIED | `generateCode()` (main.html:1598) | local `aiRequest()` | **NO**, plus a confirmed bug — see §3 |
| Photo | `photo` | flat cost, `IMAGE_GENERATION_CREDIT_COST=5` (`credit_config.py`) | VERIFIED, moved server-side in a prior phase (`_call_image_provider`, Pollinations, no API key) | `generateImage()` (main.html:1794) | local `aiRequest()` | **NO** |

All five already go through the real backend (credits, entitlement, rate
limit) — Step 0's "no direct provider calls from the browser" requirement
is already satisfied for all five, including Photo, per the code comments
in `api_views.py` documenting that migration. No dummy buttons or fake
premium state found in the code paths read so far — **NOT VERIFIED**
across the *entire* 2417-line template yet, only the generator functions
themselves; the security sweep (Step 13) still needs a full pass over the
rest of the file (nav, profile widgets, plans page).

## 3. Confirmed bug — code generator action scope (matches task's callout)

**BROKEN — verified by reading the exact braces.**

`templates/main.html` line 926 renders:
```html
<button onclick="downloadGeneratedCode()">💾 Download</button>
<button onclick="copyGeneratedCode()">📋 Copy</button>
<button onclick="resetCodeGenerator()">🔄 Reset</button>
```

But `downloadGeneratedCode()`, `copyGeneratedCode()`, and
`resetCodeGenerator()` are declared at lines 1746/1757/1763 — **nested
inside** `async function generateCode() { ... }` (opens line 1598, whose
`try/finally` body doesn't close until line 1744). Function declarations
are hoisted only within their *enclosing* scope, which here is
`generateCode()`, not `window`. The inline `onclick` handlers at line 926
run in global scope and cannot see these three functions at all —
clicking any of those three buttons throws `ReferenceError: ... is not
defined` in the console, silently (the buttons visually work but do
nothing observable beyond an uncaught error).

There's also a second, separate, *working* copy of copy/download wired
correctly at lines 1690–1727: `generateCode()` builds its own
`copyCodeBtn`/`downloadCodeBtn` buttons with `.onclick =` assigned in JS
after each generation, scoped correctly. So the feature works today only
through the buttons `generateCode()` itself renders — the three
static HTML buttons at line 926 are dead.

Fix (deferred to Step 4, not done in this pass): expose exactly one set of
actions. Cleanest: delete the static buttons at line 926 (redundant with
the dynamically-rendered pair) and rename `resetCodeGenerator`'s
call site accordingly, rather than adding a second global-namespace
patch (`window.downloadGeneratedCode = downloadGeneratedCode`), which the
task explicitly warns against re-creating.

## 4. History — real, but not generator-aware

**MISSING**, verified by reading `accounts/models.py` and
`accounts/views.py`:

- `ChatHistory` model (`accounts/models.py:5`) has exactly four fields:
  `user, query, response, created_at`. **No `feature` column.**
- `history_view()` (`accounts/views.py:160`) infers a display "type"
  (`image` / `code` / `text`) by *sniffing the response string*
  (`startswith("http")` → image; contains `<` and `>` → code; else text)
  — not from any stored feature. This will misclassify, e.g., a Story or
  Proposal result that happens to contain angle brackets, or a Code
  result in a language with no `<`/`>` at all.
- `delete_history()` (`accounts/views.py:184`) is a plain `redirect`, not
  a JSON endpoint — a fetch-based delete-from-result-view (Step 8's
  "support delete") isn't available; only the existing full-page
  `history/delete/<id>/` link works.
- `save_history()` requires `@login_required` — guests can generate but
  cannot save history (consistent with the rest of the app's guest
  model, not a bug, just a constraint the UI must reflect).
- All five generator functions in `main.html` call the same
  `saveHistorySafe(query, response)` helper (line 1569) — confirmed at
  call sites 1730, 1841, 1995, 2178, 2338 — which POSTs to
  `/save_history/` with no `feature` field, because the backend has
  nowhere to put one.

Per the task's Step 8 instruction ("if history backend lacks
functionality required by the current product design, document it first,
then implement the smallest proper backend addition") — the smallest
correct addition is one migration adding `ChatHistory.feature`
(nullable/default `""` for backward compatibility with existing rows,
populated going forward from the same `feature` slug already sent to
`/api/ai/`), used to replace the string-sniffing in `history_view()` and
to enable a real per-generator delete/reopen flow. Not yet implemented in
this pass.

## 5. Frontend foundation exists but is unused by the generator page

**CODE-VERIFIED**: `static/js/api.js` (`DS.api`) and `static/js/credits.js`
(`DS.credits`, a credit-pill widget hitting `GET /credits/balance/`) both
exist, are documented as consolidating CSRF/error handling used
elsewhere, and their own doc comments say **"Not wired into any existing
page yet — foundation only."** `main.html` still has its own local
`getCsrfToken()`/`aiRequest()` implementation and shows no credit balance
anywhere on the generator page itself (only `profile.html` shows credits
today). This is exactly the Step 5 "fetch/display current balance where
appropriate... refresh after generation" gap.

## 6. Not yet audited this pass (genuinely NOT VERIFIED)

To be honest about scope rather than inferring: the following Step 0
targets have not been read yet and are called out explicitly rather than
assumed clean —

- `templates/profile.html` (1284 lines), `templates/plans.html`,
  `templates/base_app.html`, `templates/partials/nav_app.html`
- `static/css/tokens.css` / `components.css` / `shell.css` (read sizes
  only, not content)
- `static/js/nav.js`, `static/js/toast.js`, `static/js/billing.js`
  (billing.js is loaded on `main.html`; not yet read)
- `accounts/services/entitlement_service.py`,
  `accounts/services/credit_service.py`,
  `accounts/services/ai_usage_service.py` internals (only their
  docstrings/public contract were traced via `api_views.py`, not the
  full 382/517/223-line bodies)
- The full 5 `existing` AI test files
  (`test_ai_credit_exhaustion_ui.py`, `test_ai_photo_integration.py`,
  `test_ai_rate_limit.py`, `test_ai_idempotency_conflict.py`,
  `test_ai_credit_integration.py`, `test_ai_request_validation.py`,
  `test_ai_entitlement_integration.py`, `test_ai_csrf.py`) — not run yet
- Deep-linking (`/generate/?feature=...` or equivalent) — no such route
  found in `AI_GENERATORS/urls.py` or `accounts/urls.py` yet; **MISSING**,
  needs to be added in Step 9, not merely wired.

## 7. Honest scope note

This single pass covers Step 0 and part of Step 1 only. Steps 2–16 of the
task (the full workspace rebuild across a 2417-line template, the
`ChatHistory.feature` migration + tests, deep-linking, a full security
sweep of every remaining file, accessibility, responsive verification,
the six other required documents, and a full-project ZIP with an
extract-and-`manage.py check` verification pass) have **not** been done
yet and are not being claimed as done. Continuing is the plan — this file
exists so that plan is grounded in code that was actually read, not
inferred.
