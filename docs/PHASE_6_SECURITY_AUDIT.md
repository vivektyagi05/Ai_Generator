# PHASE 6 STEP 6.10 — Security Audit

Status legend: CODE-VERIFIED · TEST-VERIFIED · PRE-EXISTING (confirmed
true before this batch, unchanged by it).

## Real finding this batch, fixed

**Stored/reflected XSS via AI-generated output in three of five
generators.** `generateStory()`, `generateApplication()`, and
`generateProposal()`'s `onSuccess` handlers inserted their AI-generated
result text into the DOM via `innerHTML` **without** `escapeHtml()`.
`generateCode()`'s own result (and its explanation text) already
correctly used `escapeHtml()` — found by reading every one of the 24
`.innerHTML =` assignment sites in `main.html` individually, rather than
treating "escapeHtml() exists in this file" as proof it was applied
everywhere it needed to be.

**Concrete exploit shape:** a user (or a prompt crafted to make the model
echo attacker-supplied markup — the model has no instruction not to
produce HTML if asked to) generates a Story/Application/Proposal whose
result text happens to contain `<script>...</script>` or an
`onerror=`-bearing tag; that markup would execute in the browser
immediately when the result rendered. Saved history was NOT
independently vulnerable (`history.html` renders through Django's default
template auto-escaping, verified separately in
`PHASE_6_HISTORY_UX_AUDIT.md`) — the gap was specifically the live
result page immediately after generation.

**Fix:** wrapped all three in `escapeHtml(...)`, matching Code's
pre-existing correct pattern exactly rather than introducing a new
one. TEST-VERIFIED two ways: (1) the exact fixed strings are present in
the rendered source, and (2) a structural regex guard
(`test_no_generator_result_still_interpolates_raw_ai_text_into_innerhtml`)
that finds every `${...}` interpolation of each raw result variable name
anywhere in the page and asserts every single one is wrapped in
`escapeHtml()` — this catches a future regression even if it appears at
a different line number than today, not just the four specific lines
fixed right now.

`accounts/tests/test_security_xss.py`, 5/5 passing.

## What was already true — CODE-VERIFIED/TEST-VERIFIED this batch, not assumed

Checked directly against the actually-touched files, not carried forward
from memory of earlier sessions:

1. **No `eval`/`new Function`** anywhere in `templates/main.html`,
   `templates/partials/*.html`, or `static/js/*.js` — grepped directly,
   zero matches.
2. **No `localStorage`/`sessionStorage`** use anywhere in those same
   files — zero matches. No client-side auth state stored this way.
3. **No hardcoded provider URLs or API keys** in any frontend file —
   `pollinations`/`groq.com`/`api.openai`/`GROQ_API_KEY`/
   `RAZORPAY_KEY_SECRET` all grepped, zero matches in
   templates/static JS. The photo provider call happens exclusively
   server-side (`_call_image_provider()` in `AI_GENERATORS/api_views.py`
   — unchanged this batch, re-confirmed by grep, not re-read line by
   line since prior sessions already traced it in full).
4. **No `window.*` global-function patches** — the one grep match was a
   comment referencing the *removed* pattern (`window.resetCodeGenerator
   = ...`), not a live instance.
5. **Deep-link generator value is whitelist-controlled server-side**
   (`AI_GENERATORS/views.py:create()`'s `_DEEP_LINK_FEATURE_TO_TAB_ID`) —
   established and tested in the 6.7 batch, re-confirmed unchanged.
6. **History cross-user isolation and its own XSS protection** —
   established and tested in the 6.6 batch (`test_history_workspace.py`),
   re-confirmed unchanged; not re-tested from scratch this batch since
   nothing in this batch touched `history.html` or its views.
7. **Credit/entitlement/cost remain server-authoritative** — the
   frontend only ever *displays* `/credits/balance/` and
   `/api/entitlements/` responses (6.5); no code path anywhere computes
   or transmits a client-chosen cost, plan, or entitlement value to
   `/api/ai/`. `/api/ai/` itself was not modified at any point across
   this entire session — every change stayed in templates and view
   context, never touched the credit/entitlement/rate-limit pipeline.
8. **CSRF remains intact** — `main.html`'s own `CSRF_TOKEN` JS constant
   (from `{{ csrf_token }}`) and `DS.api`'s cookie-based CSRF retrieval
   (with meta-tag fallback) are unchanged; the 6.8A shell migration
   removing the unconditional `<meta name="csrf-token">` tag for guests
   does not affect this, since `main.html`'s own AI-generation requests
   never relied on that meta tag in the first place (confirmed by
   reading the actual CSRF_TOKEN assignment, not assumed).

## What was NOT re-verified this batch (honest gaps)

- **No fresh penetration-style testing** (actually attempting a spoofed
  `/api/ai/` request with a forged cost/feature/entitlement value from
  outside the browser) was performed this batch — the existing
  `test_ai_*` test files cover this from earlier sessions and were
  re-run (all pass, unchanged), but no NEW spoofing test was added this
  specific batch since nothing in this batch touched the backend
  contract those tests already cover.
- **Rate-limiting bypass attempts, duplicate-submission races under real
  concurrent load** — covered by existing `test_ai_rate_limit.py` and
  the state-machine's `REQUESTING`-state dedup guard (6.3), not
  re-verified under new load-testing this batch.
- **A full re-scan of `profile.html`/`plans.html`** for the same class of
  issues (unescaped `innerHTML` insertion of user/AI-controlled content)
  was not performed — this batch's sweep was scoped to the files it
  actually touched or that share `main.html`'s generator-result
  rendering pattern.
