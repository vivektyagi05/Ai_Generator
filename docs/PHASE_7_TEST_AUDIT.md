# PHASE 7 TEST AUDIT

## Exact numbers (not estimated)

- Verified starting baseline: **560 passing, 9 skipped**.
- Current: **621 passing, 9 skipped** (verified by running
  `python manage.py test accounts` in full, most recently this session).
- Net new: **61 tests** -- 60 in 9 new files under
  `accounts/tests/test_phase7_*.py`, plus 1 added to the pre-existing
  `test_security_xss.py` to lock in the Story `<pre>` normalization.
- `python manage.py check`: clean throughout, re-verified at every
  milestone.
- `python manage.py makemigrations --check --dry-run`: no changes
  detected throughout (no model changes were made this session).

## New test files, with counts and what each actually verifies

| File | Tests | Verifies |
|---|---|---|
| `test_phase7_copy_actions.py` | 9 | Copy buttons exist, are wired to real module-scope functions, reuse the existing `copyTextToClipboard()` helper (not a duplicate), use the existing `DS.toast` (not a new notification system) |
| `test_phase7_cost_transparency.py` | 10 | `/api/ai/costs/` is public/GET-only, all 5 features present, every number matches the real `estimate_reservation_credits()` output, no internal config names leak, frontend fetch wiring is real and happens exactly once |
| `test_phase7_rate_limit_ux.py` | 6 | `Retry-After` header is actually read from the real response, carried through, never fabricated, `QUOTA_EXCEEDED` still routes to its own separate existing renderer |
| `test_phase7_empty_states_and_a11y.py` | 9 | History empty state, proposal-style-picker button conversion, `<h1>`/landmark additions, dead-code removal |
| `test_phase7_golden_ui.py` | 9 | Reduced-motion CSS, generator subtitles, live status badge wiring including confirming it never touched the Node-tested state-machine block |
| `test_phase7_required_field_labels.py` | 5 | Required/optional markers match each generator's real `validate()` function; `storyLength` correctly gets none |
| `test_phase7_workspace_shell.py` | 5 | Code generator now shares the same `.main-content/.editor-section/.sidebar` shell as the other four, with all original ids/handlers intact |
| `test_phase7_result_headings.py` | 3 | Result headings exist for all 5 generators, are DOM siblings (not children) of the output div, use one shared class |
| `test_phase7_security_sweep.py` | 4 | New `/api/ai/costs/` endpoint has no spoofable input, no credential leakage, stable/repeatable output |
| `test_security_xss.py` (+1 test) | 6 total | Extended to confirm Story's `<pre>` normalization kept the escaping property intact, and that all 4 text/code generators now use the same wrapper element |

## What these tests do NOT claim

- No test in this suite executes JavaScript (no browser available in
  this environment) -- every frontend-facing test asserts against the
  real rendered template source returned by Django's test client, the
  same verification boundary every pre-existing frontend test in this
  project already used.
- No test claims real browser or screen-reader verification.

## Duplicate-coverage check

Before writing new tests each batch, existing coverage was checked
first (not assumed) by reading the relevant pre-existing test files.
Confirmed already-thorough and NOT duplicated: generator consistency
(`test_generator_experience_matrix.py`, `test_generator_experience_consistency.py`),
CSRF (`test_ai_csrf.py`), XSS-in-history
(`test_history_workspace.py`), entitlement/credit spoofing
(`test_credit_security.py`, `test_entitlement_security.py`), deep-linking
(`test_generator_deep_linking.py`), state machine
(`test_generator_state_machine.py`).

## UPDATE (later session): current totals and new files

- **Current: 663 passing, 9 skipped** (up from 621/9 above), verified
  by running the full suite after every individual file change in that
  session, not just at the end.
- **6 more new test files plus 3 pre-existing files edited in place**
  (not counted as new): `test_phase7_spacing_tokens.py`,
  `test_visual_polish.py`, and `test_generator_lifecycle.py` each had
  1-3 assertions updated to match intentional new values (old
  page-local CSS aliases/hardcoded colors the Golden UI re-theme
  deliberately replaced), not silently overwritten — each documented
  inline with why the new assertion is correct. 38 new tests total
  across the 6 new files (verified by running exactly those 6 files
  together: `Ran 38 tests ... OK`):

| File | Tests | Verifies |
|---|---|---|
| `test_phase7_profile_shell_migration.py` | 6 | `profile.html` uses the real shared nav, offers History/Pricing (the old header didn't), no duplicated `:root` block |
| `test_phase7_history_shell_migration.py` | 5 | `history.html` uses the real shared nav, the old unrelated purple-gradient theme is gone, all of the page's own JS hooks (`statText`/`statImage`/`statCode`/etc.) survived unchanged |
| `test_phase7_plans_shell_migration.py` | 8 | `plans.html` works correctly for BOTH anonymous and authenticated visitors post-migration (the one page with that requirement), split into 2 test classes |
| `test_phase7_profile_xss_fix.py` | 3 | The self-XSS fix: API layer still returns unescaped JSON (correct), template now calls `DS.util.escapeHtml` at the actual render site, shared helper exists |
| `test_phase7_history_csrf_fix.py` | 5 | GET no longer mutates (405), POST still works exactly as before, ownership isolation still holds |
| `test_phase7_remaining_716_items.py` | 11 | The 3 previously-outstanding 7.16 items, verified against the REAL extracted JS source via Node (network-failure rendering, mid-request tab-switching safety, credit-refresh timing/mechanism) — not reimplemented guesses |

Building the last file caught its own wrong initial assumptions (guessed
function/call-site names that didn't match the real code) by re-reading
the actual source before finalizing assertions — documented in that
file's own module docstring and in the checkpoint.
