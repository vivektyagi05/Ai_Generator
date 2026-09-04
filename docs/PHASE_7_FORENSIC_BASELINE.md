# PHASE 7 FORENSIC BASELINE

Written from what was actually inspected and run, not assumed.

## Environment

- Python 3.12.3, Django 5.2, SQLite (test default).
- Tests require `SECRET_KEY` (raises on startup if unset -- pre-existing
  `AI_GENERATORS/settings.py` requirement) and `GROQ_API_KEY` (Phase 6
  requirement -- `AI_GENERATORS/api_views.py` returns HTTP 500 before
  reaching credit/entitlement logic if unset; not a defect, an
  environment prerequisite for the AI-provider integration tests). Never
  a real key used in this environment.

## Verified starting state

- `python manage.py check`: clean, 0 issues.
- `python manage.py makemigrations --check --dry-run`: no changes
  detected.
- `python manage.py test accounts`: **560 tests, all passing, 9 skipped**
  (Postgres-only concurrency tests -- self-skip on SQLite per their own
  docstrings, not a gap this session introduced or needs to close).

## Real, existing architecture confirmed by reading the actual source

- Canonical generator lifecycle: `runGeneratorLifecycle()` in
  `templates/main.html`, used by all five generators (Code/Photo/Story/
  Application/Proposal) -- one controller, not five.
- State machine: `GENERATOR_STATES` enum + `setGeneratorState()` +
  `classifyFailureState()`, inside a block explicitly marked
  `PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_START/END` and extracted
  verbatim for execution under Node by
  `accounts/tests/test_generator_state_machine.py`. Treated as
  untouchable architecture throughout this session except for reading
  it -- confirmed via a dedicated test
  (`test_phase7_golden_ui.py::test_status_badge_does_not_touch_the_node_tested_state_machine_block`)
  that later additions never touched this block.
- Credit cost: single source of truth is
  `accounts/credit_config.py::estimate_reservation_credits()`, already
  called by `accounts/services/ai_usage_service.py` at real reservation
  time. Phase 6 had NO way to expose this to the frontend before
  submission -- confirmed by grep, not assumed.
- Entitlement: `accounts/entitlement_views.py` (`entitlements_view`,
  `plans_view`), `accounts/services/entitlement_service.py`.
- History: `ChatHistory` model, reopen via `?generator=` deep-link
  (`accounts/urls.py`, `templates/history.html`), already thoroughly
  tested (`test_history_workspace.py`, 13 tests including a real,
  behavioral XSS-injection test on both the history query and the
  rendered response).
- CSRF: already covered with a real `enforce_csrf_checks=True` client
  test (`test_ai_csrf.py`).

## Real gaps found (the actual reason Phase 7 work was needed)

1. No cost/credit information was ever shown before submission.
2. Plain rate-limit (429) responses fell into a generic red-text error
   path even though the backend already sent a real `Retry-After`
   header that the frontend never read.
3. History's empty state was the bare string "No history found." with
   no next step.
4. The proposal style picker was four `<div onclick>` elements -- not
   keyboard-focusable, invisible to assistive tech as a control.
5. Zero `<h1>` anywhere in the workspace template.
6. The generator-selector tab strip had no accessible name.
7. Zero `prefers-reduced-motion` handling across four infinite
   animations plus hover transforms.
8. Only 2 of 18 field labels indicated required/optional status, and
   inconsistently (hand-typed text vs. nothing) -- including one field
   (`proposalDetails`) that was genuinely optional but had no indication
   either way.
9. `selectStoryType()` was declared but fully unreachable dead code.
10. Code was the one generator not using the shared
    `.main-content > .editor-section + .sidebar` workspace shell every
    other generator used.
11. None of the five result areas had a heading marking where the result
    begins.
12. Story's result wrapper used `<div class="pretty-output">` while
    Code/Application/Proposal used `<pre>` -- functionally identical
    (CSS forces `white-space:pre-wrap` regardless of tag) but
    semantically inconsistent.

## Explicitly checked and found ALREADY SOLID (not re-implemented)

- Generator consistency (state, credit refresh, entitlement, error
  routing) across all five: `test_generator_experience_matrix.py` +
  `test_generator_experience_consistency.py`.
- CSRF, XSS-in-history, XSS-in-generated-content (Code/Story/
  Application/Proposal all already escaped their AI-generated output via
  `escapeHtml()` before this session started), provider-credential
  exposure, entitlement/credit-spoofing resistance: all pre-existing,
  real, behavioral tests -- not re-implemented, only read and confirmed.
- Code's result presentation (syntax-friendly `<pre>`, monospace font,
  `overflow-x:auto`, working Copy/Download): already solid before this
  session.
- The `validate() -> alert()` pattern on validation failure: confirmed
  via the lifecycle's own docstring as intentional, documented Phase 6
  design -- deliberately left untouched.
