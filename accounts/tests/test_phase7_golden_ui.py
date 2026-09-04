"""
PHASE 7 GOLDEN UI (this batch) -- three real, additive changes to
templates/main.html, verified the same way the rest of this suite
verifies frontend contracts: rendering the real template server-side
and asserting on the emitted source, since no browser is available here
(marked EXTERNAL VERIFICATION REQUIRED for actual rendered/visual
confirmation).

  1. prefers-reduced-motion: the page ran four infinite animations plus
     several hover transforms with zero motion-reduction handling.
  2. Every generator now states its short purpose under its title (2C).
  3. A live status badge, reusing the EXISTING GENERATOR_STATES enum and
     the EXISTING cost-hint element each generator already had (not a
     second state system, not five new DOM nodes) -- shows "Generating…"
     only while runGeneratorLifecycle() is actually REQUESTING, restored
     to the real cost line via the finally block, which runs on every
     exit path (success, failure, or an unhandled exception).
"""

from django.test import TestCase


class ReducedMotionTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_prefers_reduced_motion_media_query_exists(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.html)

    def test_reduced_motion_block_disables_animation_and_transition_duration(self):
        start = self.html.index("@media (prefers-reduced-motion: reduce)")
        end = self.html.index("</style>", start)
        block = self.html[start:end]
        self.assertIn("animation-duration: 0.001ms", block)
        self.assertIn("transition-duration: 0.001ms", block)


class GeneratorSubtitleTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_every_generator_has_a_real_subtitle_describing_its_purpose(self):
        for expected_snippet in (
            "get working, runnable code",
            "generate an original AI image",
            "complete, illustrated-in-words short story",
            "formal, ready-to-send application letter",
            "structured, professional proposal document",
        ):
            self.assertIn(expected_snippet, self.html)

    def test_subtitles_use_the_shared_class_not_five_one_off_styles(self):
        import re

        self.assertEqual(len(re.findall(r'class="generator-subtitle"', self.html)), 5)


class LiveStatusBadgeWiringTests(TestCase):
    """
    Verifies the badge reuses GENERATOR_STATES's REQUESTING concept (via
    runGeneratorLifecycle's own existing setGeneratorState(genState,
    GENERATOR_STATES.REQUESTING) call -- untouched here) rather than
    introducing a second, parallel notion of "is it loading".
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_status_badge_function_is_real_and_module_scope(self):
        self.assertIn("function setGeneratorStatusBadge(", self.html)

    def test_status_badge_is_set_at_the_real_requesting_transition(self):
        start = self.html.index("setGeneratorState(genState, GENERATOR_STATES.REQUESTING);")
        end = self.html.index("try {\n                const result = await aiRequest", start)
        block = self.html[start:end]
        self.assertIn("setGeneratorStatusBadge(config.statusBadgeId, config.feature, true)", block)

    def test_status_badge_is_cleared_in_the_finally_block_not_only_on_success(self):
        # The finally block runs on every exit path (success, failure, or
        # an unhandled exception) -- this is the one correct place, not a
        # duplicated call after each individual branch. PHASE 8 STEP 8/9
        # wrapped the body in `if (!stale)` (a stale/superseded attempt
        # must not touch a button/badge a newer attempt now owns -- see
        # runGeneratorLifecycle's own comment) so the anchors here were
        # updated to the new real text; the assertion itself (the badge
        # clear call is present in this block) is unchanged.
        finally_start = self.html.index("} finally {\n                // A stale attempt")
        finally_end = self.html.index("\n        }\n\n        // PHASE 8 STEP 8/9")
        block = self.html[finally_start:finally_end]
        self.assertIn("setGeneratorStatusBadge(config.statusBadgeId, config.feature, false)", block)

    def test_all_five_generators_pass_a_status_badge_id_matching_their_own_cost_hint(self):
        expected = {
            "codeGenState": "codeCostHint",
            "photoGenState": "photoCostHint",
            "storyGenState": "storyCostHint",
            "applicationGenState": "applicationCostHint",
            "proposalGenState": "proposalCostHint",
        }
        for gen_state, hint_id in expected.items():
            call_start = self.html.index(f"runGeneratorLifecycle({gen_state}, {{")
            call_end = self.html.index("validate:", call_start)
            call_block = self.html[call_start:call_end]
            self.assertIn(
                f'statusBadgeId: "{hint_id}"',
                call_block,
                f"{gen_state}'s runGeneratorLifecycle call is missing statusBadgeId: \"{hint_id}\"",
            )

    def test_status_badge_does_not_touch_the_node_tested_state_machine_block(self):
        # setGeneratorState()/GENERATOR_STATES/classifyFailureState() --
        # the block accounts/tests/test_generator_state_machine.py
        # extracts and executes under Node -- must remain untouched by
        # this batch's additions.
        start = self.html.index("PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_START")
        end = self.html.index("PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_END")
        block = self.html[start:end]
        self.assertNotIn("setGeneratorStatusBadge", block)
        self.assertNotIn("statusBadgeId", block)
