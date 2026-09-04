"""
PHASE 7 STEP 7.8 (empty states) / 7.13 (accessibility) -- two real, scoped
findings from the fresh forensic pass:

  1. history.html's empty state was the generic "No history found." with
     no next step -- fixed to explain what happened and give a real,
     working link back to the workspace (not decorative).
  2. The proposal style picker (Professional/Creative/Technical/Executive
     Summary) was four <div onclick=...> elements -- not natively
     keyboard-focusable or activatable via Enter/Space, invisible to
     assistive tech as an interactive control. Converted to real
     <button type="button"> elements (prefer native HTML semantics over
     adding ARIA, per the Phase 7 accessibility brief) with CSS restoring
     the identical visual look.
"""

from django.test import TestCase


class HistoryEmptyStateTests(TestCase):
    def test_empty_history_explains_what_happened_and_what_to_do_next(self):
        # history_view() requires login; an unauthenticated request
        # redirects rather than rendering the page, so log in first to
        # reach the real empty state for a fresh user with zero entries.
        from django.contrib.auth.models import User

        User.objects.create_user(username="freshuser", password="x")
        self.client.login(username="freshuser", password="x")
        html = self.client.get("/history/").content.decode()

        self.assertNotIn("No history found.", html)
        self.assertIn("No generations yet", html)
        # Real, working link back to the workspace, not decorative text.
        self.assertIn('href="/home/"', html)


class ProposalStylePickerAccessibilityTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_style_options_are_real_buttons_not_divs(self):
        for style in ("professional", "creative", "technical", "executive"):
            self.assertIn(f"onclick=\"selectProposalStyle('{style}')\"", self.html)
        # None of the four remain a <div onclick="selectProposalStyle(...)">.
        self.assertNotIn('<div class="content-option" onclick="selectProposalStyle(', self.html)

    def test_style_option_buttons_are_type_button_not_submit(self):
        import re

        matches = re.findall(
            r'<button type="button" class="content-option" onclick="selectProposalStyle\(',
            self.html,
        )
        self.assertEqual(len(matches), 4)

    def test_style_option_function_still_declared_and_reachable(self):
        self.assertIn("function selectProposalStyle(", self.html)


class HeadingHierarchyAndLandmarkTests(TestCase):
    """
    PHASE 7 STEP 7.13 -- the page had zero <h1> anywhere (every generator's
    own title started at <h2>) and the generator-selector tab strip had no
    accessible name identifying what it is. Both fixed with plain,
    standard HTML semantics (a visually-hidden real <h1>, a labeled
    navigation landmark) rather than added ARIA beyond what's needed.
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_page_has_exactly_one_real_h1(self):
        import re

        self.assertEqual(len(re.findall(r"<h1[ >]", self.html)), 1)
        self.assertIn('<h1 class="sr-only">', self.html)

    def test_h1_appears_before_every_generator_h2(self):
        h1_pos = self.html.index("<h1")
        for h2_marker in (
            "AI Code Creator",
            "AI Photo Generator",
            "AI Story Generator",
            "Application Writer",
            "AI Proposal Maker",
        ):
            self.assertLess(h1_pos, self.html.index(h2_marker))

    def test_generator_selector_has_an_accessible_name(self):
        self.assertIn('aria-label="Choose generator"', self.html)


class DeadCodeSweepTests(TestCase):
    """
    PHASE 7 STEP 7.17 fresh dead-code sweep finding: selectStoryType() was
    declared but genuinely unreachable (no element in the Story tab ever
    referenced it, and its own currentStoryType assignment targeted a
    variable that was never declared anywhere). Removed. Locked in here so
    a future edit doesn't reintroduce it without a real caller.
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_dead_select_story_type_function_is_gone(self):
        self.assertNotIn("function selectStoryType(", self.html)
        # Only the removal comment may still mention the variable name;
        # it must not appear as a live assignment/reference anymore.
        self.assertNotIn("currentStoryType = type", self.html)
        self.assertNotIn("currentStoryType;", self.html)

    def test_story_generator_state_and_lifecycle_are_unaffected(self):
        # The removal must not have touched anything after it in the file
        # -- storyGenState and generateStory() must still be present and
        # intact.
        self.assertIn("const storyGenState = { requestId: null, status: GENERATOR_STATES.IDLE };", self.html)
        self.assertIn("async function generateStory(event) {", self.html)
