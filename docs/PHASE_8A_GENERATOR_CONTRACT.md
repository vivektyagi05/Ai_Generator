# PHASE 8A GENERATOR CONTRACT MATRIX

Traced fresh this phase (Item 3), not copied from an earlier phase's
audit. All 5 rows share one controller (`runGeneratorLifecycle()`,
`templates/main.html`) and one backend endpoint (`POST /api/ai/`,
`AI_GENERATORS/api_views.py:ai_generate()`) — differences are isolated
to the columns below, nothing else.

| | Photo | Code | Story | Application | Proposal |
|---|---|---|---|---|---|
| Frontend fn | `generateImage()` | `generateCode()` | `generateStory()` | `generateApplication()` | `generateProposal()` |
| Feature slug | `photo` | `code` | `story` | `application` | `proposal` |
| Matches `ai_request_config.ALLOWED_FEATURES`? | yes | yes | yes | yes | yes |
| Matches `TAB_ID_TO_GENERATOR_PARAM`? | yes | yes | yes | yes | yes |
| Endpoint | `/api/ai/` | `/api/ai/` | `/api/ai/` | `/api/ai/` | `/api/ai/` |
| Method | POST | POST | POST | POST | POST |
| Provider | image provider | Groq | Groq | Groq | Groq |
| CSRF | `DS.api` helper (shared) | same | same | same | same |
| Entitlement check | shared `_check_access_policy()` | same | same | same | same |
| Credit cost | server-computed (`/api/ai/costs/`) | same mechanism | same | same | same |
| Response keys consumed | `result`/`credits_charged`/`credits_available` (+ image URL) | `result`/`credits_charged`/`credits_available` | same | same | same |
| Output rendering | image element | `escapeHtml()` into `<pre>` | `escapeHtml()` into `<pre>` | `escapeHtml()` into `<pre>` | `escapeHtml()` into `<pre>` |
| History save | shared `save_history` call | same | same | same | same |
| Retry | shared `retryFn` → `runGeneratorLifecycle` | same | same | same | same |
| Reset | `resetImageGenerator()` (module scope) | `resetCodeGenerator()` (module scope) | analogous | analogous | analogous |
| Duplicate-submit guard | shared state machine (`GENERATOR_STATES`) | same | same | same | same |

## Verification method (Item 3)

1. Static trace: every DOM id referenced by each generator's JS exists
   exactly once in the rendered template (grep-verified — no id
   collisions, no missing elements).
2. Every `onclick`/handler function referenced is declared exactly
   once, at module scope (no function nested inside another's closure
   the way the original Phase 1/6-era bug class worked — see
   `test_code_generator_actions.py`/`test_no_dead_ui.py` for that
   already-fixed history).
3. Live re-verification: real authenticated `Client()` POSTs to
   `/api/ai/` for all 4 text generators (provider HTTP mocked), 200
   response with the exact shape the frontend consumes, credits
   deducted correctly and sequentially from one shared account across
   generators (not per-generator silos). See
   `accounts/tests/test_phase8a_generator_reverification.py`.
4. Photo re-verified via the pre-existing
   `test_ai_photo_integration.py` (unaffected by this phase's changes,
   still green) — not duplicated in the new file.

## Conclusion

No divergence found in any of the 5 rows. The "Story/Application/
Proposal/Code not working" report from the master prompt was not
reproducible against this repository snapshot's code. Most likely
explanation (documented, not asserted as fact): the Item 2 guest-credit
bug's confusing 0→45 flicker, as experienced by an unauthenticated
visitor, or an environment-config issue (missing `GROQ_API_KEY`, stale
static files) in whatever deployment produced the original report — see
PHASE_8A_PROGRESS.md Item 3 for the full reasoning.
