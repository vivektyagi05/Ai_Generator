# PHASE 7 ACCESSIBILITY AUDIT

Code-level pass only -- **EXTERNAL VERIFICATION REQUIRED** for real
screen-reader/browser confirmation; no browser or assistive-tech tooling
is available in this environment. Everything below was verified by
reading the rendered template output and via structural Django tests,
not claimed as manually tested with real assistive technology.

## Findings and fixes (real, not decorative)

| Finding | Fix | Test |
|---|---|---|
| Proposal style picker was 4x `<div onclick>` -- not keyboard-focusable, invisible to AT as a control | Converted to real `<button type="button">`, CSS restores identical visual look | `test_phase7_empty_states_and_a11y.py::ProposalStylePickerAccessibilityTests` |
| Zero `<h1>` anywhere in the workspace | Added one real, visually-hidden `<h1>` (standard accessible pattern -- present in DOM/AT, not shown visually, since a visible one would duplicate the nav bar's own brand text) | `HeadingHierarchyAndLandmarkTests::test_page_has_exactly_one_real_h1` |
| Generator-selector tab strip had no accessible name | Added `role="navigation" aria-label="Choose generator"` | `test_generator_selector_has_an_accessible_name` |
| Zero `prefers-reduced-motion` handling across 4 infinite animations + hover transforms | Added a `@media (prefers-reduced-motion: reduce)` block collapsing animation/transition duration and disabling hover-transform on the relevant selectors | `test_phase7_golden_ui.py::ReducedMotionTests` |
| Only 2/18 field labels indicated required/optional status, inconsistently | All 12 truly-required fields get a real marker (visual `*` + screen-reader-only "required" text, not a `title` attribute, which isn't reliably announced); all 4 truly-optional fields use one shared `.optional-hint` class. Checked against each generator's real `validate()` function, not guessed. `storyLength` (a `<select>`, always has a value) correctly got no marker. | `test_phase7_required_field_labels.py` (5 tests) |
| History's empty state gave no next step | "No generations yet" + explanation + real working link back to the workspace | `test_phase7_empty_states_and_a11y.py::HistoryEmptyStateTests` |
| No result-section heading anywhere (only placeholder text inside the div, which disappears on real content) | One shared `<h3 class="result-heading">Result</h3>` as a DOM sibling (not child, so reset functions can't wipe it) before each of the five output areas | `test_phase7_result_headings.py` (3 tests) |

## Preserved, not regressed

- `aria-current` on generator tabs: pre-existing, untouched.
- `aria-live="polite"` on all five output areas and the entitlement
  banner: pre-existing, untouched.
- Semantic `<label for="...">` associations: pre-existing on all fields,
  extended (not replaced) with required/optional markers.
- No ARIA added beyond what native HTML semantics couldn't already
  provide -- e.g. the proposal-style-picker fix used a real `<button>`
  instead of `role="button"` on a `<div>`.

## Explicit checklist

- [x] Labels -- present pre-existing, required/optional status added
      this session.
- [x] aria-label -- added to generator selector.
- [x] aria-current -- pre-existing, confirmed present, untouched.
- [x] aria-live -- pre-existing, confirmed present, untouched.
- [x] Keyboard navigation -- proposal style picker fixed; all other
      interactive controls were already real `<button>`/`<a>` elements
      (confirmed by the `test_no_dead_ui.py` dynamic scan run
      throughout this session).
- [x] Focus states -- no pre-existing focus-state CSS was removed;
      not independently re-audited this session (no finding to report).
- [x] Button semantics -- fixed for proposal style picker; nothing else
      found using a non-button element as a clickable control.
- [x] Form semantics -- pre-existing, extended with required markers.
- [x] Error announcements -- output areas' pre-existing `aria-live`
      covers error rendering too, since errors render into the same
      element.
- [x] Modal focus behavior / Escape behavior -- **EXTERNAL VERIFICATION
      REQUIRED**; this workspace has no modal dialogs to audit.
- [x] Heading hierarchy -- fixed (added the missing `<h1>`; existing
      `<h2>`/`<h3>` structure was otherwise already correct).
- [x] Reduced motion -- fixed, previously entirely absent.

## Explicitly marked EXTERNAL VERIFICATION REQUIRED

- Real screen-reader confirmation (NVDA/JAWS/VoiceOver) that the
  `sr-only` `<h1>`, the required-field screen-reader text, and
  `aria-live` announcements actually read as intended.
- Real keyboard-only navigation walkthrough in an actual browser.
- Real visual confirmation of focus-state visibility and contrast.

No claim of "browser verified" or "screen-reader verified" is made
anywhere in this documentation set, per Rule 17/18 of the Phase 7 brief.
