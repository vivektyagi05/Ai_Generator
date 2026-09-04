"""
PHASE 8 STEP 8/9 -- Request/state robustness: reset during an in-flight
request must not let that request's eventual response overwrite the
now-reset workspace, and must not leave a generator's button/state stuck.

Cannot execute browser JS from a Django test (same limitation this
codebase's other frontend-contract tests document), so this follows the
established convention: assert on the exact rendered source proving the
staleness guard and the shared reset helper are wired correctly.

Covers:
  - runGeneratorLifecycle() captures a per-attempt id and checks it
    (rather than blindly rendering) both on success/failure and in the
    exception handler, and skips touching the button/badge in `finally`
    when stale
  - resetGeneratorWorkspace() clears requestId and only transitions to
    IDLE through the real state-machine guard (setGeneratorState()),
    never by assigning genState.status directly
  - all five generators' Reset functions call the shared helper with
    THEIR OWN genState (not a copy-pasted wrong one)
  - Photo's reset no longer force-enables the Generate button (the
    specific bug this batch found: forcing it open during an in-flight
    request let a second click silently no-op instead of giving any
    feedback)
"""

from django.test import TestCase


class ResetDuringRequestRobustnessTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_lifecycle_captures_and_checks_request_id_at_settle_time(self):
        self.assertIn("const myRequestId = genState.requestId;", self.html)
        self.assertIn("let stale = false;", self.html)
        # Checked after the await resolves, before any DOM/state mutation.
        self.assertIn(
            "if (genState.requestId !== myRequestId) { stale = true; return; }", self.html
        )

    def test_finally_skips_button_and_badge_cleanup_when_stale(self):
        self.assertIn("if (!stale) {", self.html)

    def test_shared_reset_helper_uses_real_state_machine_guard(self):
        self.assertIn("function resetGeneratorWorkspace(genState) {", self.html)
        self.assertIn("genState.requestId = null;", self.html)
        # Must go through setGeneratorState() (validated transition), never
        # a direct `genState.status = ...` assignment.
        self.assertIn(
            "if (genState.status !== GENERATOR_STATES.IDLE) {\n"
            "                setGeneratorState(genState, GENERATOR_STATES.IDLE);",
            self.html,
        )

    def test_every_generator_reset_calls_the_shared_helper_with_its_own_state(self):
        expected = {
            "resetCodeGenerator": "codeGenState",
            "resetEditor": "photoGenState",
            "resetStory": "storyGenState",
            "resetApplication": "applicationGenState",
            "resetProposal": "proposalGenState",
        }
        for fn_name, gen_state in expected.items():
            start = self.html.index(f"function {fn_name}(")
            end = self.html.find("\n            }", start)
            block = self.html[start:end]
            self.assertIn(
                f"resetGeneratorWorkspace({gen_state})",
                block,
                f"{fn_name} does not reset its own {gen_state} via the shared helper",
            )

    def test_photo_reset_no_longer_force_enables_button(self):
        start = self.html.index("function resetEditor() {")
        end = self.html.find("\n            }", start)
        block = self.html[start:end]
        self.assertNotIn("generateBtn.disabled = false", block)
        # Still resets the visible button text immediately -- only the
        # forced re-enable was the bug.
        self.assertIn('generateBtn.innerText = "🚀 Generate AI Photo";', block)
