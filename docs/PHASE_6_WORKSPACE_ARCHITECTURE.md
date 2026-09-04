# PHASE 6 — Workspace Architecture (6.1)

This documents the REAL current architecture, read directly out of
`templates/main.html`, not a target/aspirational one. The correction this
document makes to the prior prompts' framing: **a shared request/error
lifecycle already exists and is already used by all five generators** —
it was not discovered until this pass because it isn't packaged as one
named object. What's actually duplicated is smaller than "the whole
lifecycle."

## What's already shared (CODE-VERIFIED — grepped every call site)

```
Generator function (5x, one per generator)
    ↓ builds a feature-specific prompt string
aiRequest(prompt, {feature, requestId})      <- ONE function, all 5 call it
    ↓ prompt-length check, CSRF, AbortController timeout,
    ↓ JSON-parse safety, response.ok normalization
    ↓ returns {ok, status, data, error} uniformly
    ├─ success → generator-specific result parsing/rendering
    └─ failure → renderAiFailure(outputEl, result)  <- ONE function, all 5 call it
                     ↓ extractApiError() normalizes both /api/ai/ error
                       body shapes (string vs {code,message})
                     ├─ 401 → renderSignInRequired()      <- shared
                     ├─ 402/429 QUOTA_EXCEEDED/            <- shared
                     │  403 PLAN_NOT_ALLOWED → renderExhaustionCard()
                     │      → BillingFlow (same one plans.html/profile.html use)
                     └─ everything else → generic escaped error text
```

`escapeHtml`, `newAiRequestId`, `isExhaustionError` are also module-level
and shared. This is a real, working, DRY error-handling layer — Steps
6.2/6.3's request-lifecycle centralization is **already mostly done**,
just not by that name.

## What's actually still duplicated (the real remaining work)

Each of the 5 generator functions independently repeats this ~15-line
wrapper around the shared core above:

```js
if (fooGenState.isGenerating) return;           // dedup guard
if (!fooGenState.requestId) fooGenState.requestId = newAiRequestId();
fooGenState.isGenerating = true;
if (genBtn) genBtn.disabled = true;
outputEl.innerHTML = "<div class='loading'>...</div>";
try {
  const result = await aiRequest(prompt, {feature, requestId: fooGenState.requestId});
  if (!result.ok) { renderAiFailure(outputEl, result); return; }
  fooGenState.requestId = null;
  refreshCreditPill();
  /* generator-specific result rendering */
  saveHistorySafe(query, response, feature);
} catch (e) {
  /* generic catch */
} finally {
  fooGenState.isGenerating = false;
  if (genBtn) genBtn.disabled = false;
}
```

Five near-identical copies, each with generator-specific prompt-building
and result-rendering spliced into the middle. This — not the request/error
core — is the genuine 6.2 target: one shared orchestration function that
takes the generator-specific parts (build prompt, render success) as
callbacks and owns the dedup/button/credit/history boilerplate itself.

## 6.2 status — DONE, all five generators migrated

`runGeneratorLifecycle()` (see `templates/main.html`) is the single
orchestration function now used by **all five** generators (Code, Story,
Application, Proposal, Photo) — not just Code as an earlier draft of this
document said. Two real, deliberately-preserved differences across the
originals were carried into the shared controller as config options
rather than erased by force-fitting one shape onto all five:

- **`skipOutputLoading`**: Story/Application/Proposal/Photo never touched
  their output element while loading in the original code (only the
  button's own text changed) — Code did (a real loading placeholder).
  The controller preserves both behaviors per-generator instead of
  picking one and applying it to all five.
- **`onException`**: Story/Application/Proposal render
  `Error: ${err.message}` on an unexpected exception; Code/Photo render a
  generic "Something went wrong" message (Photo's own exact original
  wording, not Code's — they differ from each other too:
  `color:red` single-line vs `color:#ffb703` multi-line). Both original
  texts survive verbatim via the `onException` override.

TEST-VERIFIED (`accounts/tests/test_generator_lifecycle.py`, 8 tests):
every generator is wired through the controller, the old per-generator
dedup-guard lines are gone (not duplicated alongside the new controller),
Photo still never touches the image provider directly, and both
generator-specific text differences above are still present verbatim in
the rendered page.

## 6.3 status — DONE

A real, named state machine now backs every generator: `GENERATOR_STATES`
(11 states matching the master prompt's list exactly),
`GENERATOR_STATE_TRANSITIONS` (an explicit adjacency table — REQUESTING
can only be reached from VALIDATING and can only resolve to SUCCESS or
one of 7 specific failure states; every terminal state can go to
VALIDATING (retry) or IDLE (reset); IDLE can only start a new attempt via
VALIDATING), and `setGeneratorState()` — the **only** function in the
file permitted to assign `genState.status` (CODE-VERIFIED: every one of
the five `const *GenState = {...}` declarations initializes
`status: GENERATOR_STATES.IDLE`; `resetCodeGenerator()` and `switchTab()`
are the only other two call sites, and both go through
`setGeneratorState()`, never direct assignment).

`classifyFailureState()` maps a failed `aiRequest()` result onto the
correct specific failure state, reusing the exact same status/code
checks `renderAiFailure()` already uses — so the state machine and the
rendered UI can never disagree about what kind of failure just happened.

`switchTab()` resets any generator's terminal state back to IDLE when
the user switches away, satisfying "switching generator resets
incompatible state" — a generator still `REQUESTING` is deliberately
left alone (switching tabs doesn't cancel an in-flight request; its
credit commit/refund still needs to land correctly when it resolves).

**TEST-VERIFIED with real execution, not string matching**
(`accounts/tests/test_generator_state_machine.py`, 8 tests): this file
extracts the exact state-machine source out of the rendered page between
two marker comments and runs it under Node — proving actual transition
behavior (invalid transitions rejected and status left unchanged, every
`REQUESTING` outcome reachable, every terminal state can retry/reset,
`REQUESTING` structurally cannot resolve to itself or back to
`VALIDATING`) and that `classifyFailureState()` agrees with the seven
real response shapes `AI_GENERATORS/api_views.py` actually produces.

## 6.4 status — DONE (folded into 6.2 above; all five generators are the migration)

## 6.5–6.10 — NOT DONE

Deeper entitlement UX beyond the credit pill, history reopen/delete
confirmation UI, the visual rebuild onto `tokens.css`/`components.css`,
responsive/accessibility work, and the post-implementation security
sweep have not been attempted this batch.
