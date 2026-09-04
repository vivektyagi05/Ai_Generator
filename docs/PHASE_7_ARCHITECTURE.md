# PHASE 7 ARCHITECTURE

Describes the actual implementation as it exists now, not an idealized
design. Written from reading the real source, not from the original
Phase 7 brief's assumptions about what should exist.

## Single canonical generator controller

All five generators (Code, Photo, Story, Application, Proposal) call the
same `runGeneratorLifecycle(genState, config)` function in
`templates/main.html`. No second controller was created this session --
every Phase 7 addition (cost hints, status badges, result headings)
threads through this same function via new, additive, optional `config`
fields (`statusBadgeId`), never a parallel code path.

```
generateCode() / generateImage() / generateStory() /
generateApplication() / generateProposal()
        │
        ▼
runGeneratorLifecycle(genState, config)
        │
        ├─ setGeneratorState(genState, VALIDATING)
        ├─ config.validate() -- generator-specific, returns alert() text
        ├─ setGeneratorState(genState, REQUESTING)
        │     └─ config.statusBadgeId → setGeneratorStatusBadge(..., true)
        ├─ aiRequest(prompt, {feature, requestId})  →  POST /api/ai/
        ├─ on success: config.onSuccess(data)
        ├─ on failure: renderAiFailure(outputEl, result)
        │     ├─ 401            → renderSignInRequired()
        │     ├─ quota/exhausted → renderExhaustionCard()
        │     ├─ 429 (plain)    → renderRateLimited()  [NEW, this session]
        │     └─ other          → generic error span
        └─ finally: re-enable button, config.statusBadgeId →
              setGeneratorStatusBadge(..., false)  [NEW, this session]
```

## State machine

`GENERATOR_STATES` + `setGeneratorState()` + `classifyFailureState()`
live inside a block marked `PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_START`/
`_END`, extracted verbatim and executed under Node by
`test_generator_state_machine.py`. This session added a UI-level status
badge that reflects REQUESTING/settled, but implemented it entirely
OUTSIDE that block, in `runGeneratorLifecycle()`, calling the existing
`setGeneratorState()` transitions rather than introducing a second
notion of state. Verified by a dedicated test that the new code never
appears inside the extracted block.

## Cost/credit architecture

- Single source of truth: `accounts/credit_config.py::
  estimate_reservation_credits()`.
- Real reservation path: `accounts/services/ai_usage_service.py` calls
  it directly.
- New this session: `GET /api/ai/costs/`
  (`accounts/entitlement_views.py::costs_view`) calls the SAME function
  to expose it to the frontend BEFORE submission. Not a second formula --
  the same one, exposed read-only.
- Frontend: `loadGeneratorCostHints()` fetches this once per page load,
  caches the result in `LAST_FETCHED_GENERATOR_COSTS`, and both
  `renderCostHint()` and `setGeneratorStatusBadge()` read from that
  cache -- no repeated network calls per keystroke or tab switch.

## Workspace shell (HTML structure)

All five generators now share:

```
<div class="tab-content" id="{generator}-tab">
  <div class="main-content">              [display:grid, 1fr 300px]
    <div class="editor-section">
      <h2>{title}</h2>
      <p class="generator-subtitle">{purpose}</p>   [NEW]
      <div class="input-area">...</div>             (repeated per field)
      <button class="generate-btn">Generate</button>
      <p class="generator-cost-hint">...</p>         [NEW]
      <h3 class="result-heading">Result</h3>         [NEW]
      <div class="output-area" aria-live="polite">...</div>
    </div>
    <div class="sidebar">
      <div class="body-options">
        <h3>Quick Actions</h3>
        <button>...</button>  (Copy/Download/Share/Reset -- generator-specific)
      </div>
    </div>
  </div>
</div>
```

Before this session, Code was the one exception (flat single-column
markup, no `.sidebar`). Fixed to match; this is the one structural
(not just additive) HTML change made this session.

## History / deep-linking

Unchanged this session except for the empty-state copy in
`templates/history.html`. Reopen still uses the same
`?generator={feature}` deep-link mechanism, `switchTab()` still the only
router.

## What was deliberately NOT touched

- `/api/ai/` business logic, credit calculation internals, entitlement
  logic, authentication, the AI provider integration, `switchTab()`
  itself, `runGeneratorLifecycle()`'s core control flow, the
  `GENERATOR_STATES` enum/state-machine block, and the
  `validate() -> alert()` contract (confirmed intentional via its own
  docstring).
