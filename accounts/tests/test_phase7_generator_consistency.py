"""
PHASE 7 Golden UI, generator consistency pass -- found several real,
specific inconsistencies by diffing all 5 generators' markup against
each other (not assumed):

1. Code's title/subtitle used inline styles duplicating what the
   shared .tab-title/.generator-subtitle classes already provided for
   the other 4 generators -- and .tab-title itself had no text-align,
   so 4 of 5 generators had a left-aligned title above a centered
   subtitle (an inconsistency inside each panel, not just across
   generators). Fixed by centering .tab-title itself and converting
   Code to use the shared classes like the other 4 already did.
2. The "waiting for a result" placeholder paragraph existed in all 5
   generators but was only styled (color + center) on Code (via inline
   style) and Photo (via a parent-level inline style this pass
   replaced) -- Story/Application/Proposal's had zero styling. One
   shared .output-placeholder class now covers all 5, in both their
   initial markup AND their reset-button JS (which previously
   restored the unstyled version, undoing any fix that only touched
   the initial markup).
3. Removing Photo's parent-level `style="text-align:center"` exposed
   that `.preview-image` (the actual generated photo) had NO CSS rule
   at all -- no responsive max-width, centered only by accident via
   the now-removed parent style. Given an explicit rule.
4. Code's two `<label>`s had a redundant inline
   `style="color:var(--text-primary)"` duplicating what
   `.input-area label` already provides.
5. Code's copy/download button row had the one remaining inline
   `style="display:flex; gap:10px; margin-bottom:12px;"` in the whole
   re-themed workspace -- moved into a real `.code-actions` class.
"""

from django.test import TestCase


class TitleSubtitleConsistencyTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_generators_use_the_shared_title_class_not_inline_styles(self):
        for expected in (
            '<h2 class="tab-title">💻 AI Code Creator</h2>',
            '<h2 class="tab-title">AI Photo Generator</h2>',
            '<h2 class="tab-title"> AI Story Generator</h2>',
            '<h2 class="tab-title">📝 Application Writer</h2>',
            '<h2 class="tab-title">📑 AI Proposal Maker</h2>',
        ):
            self.assertIn(expected, self.html)
        # No generator should have its own inline title styling anymore.
        self.assertNotIn('<h2 style=', self.html)

    def test_tab_title_itself_is_centered_matching_the_subtitle_beneath_it(self):
        start = self.html.index(".tab-title {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("text-align: center", block)

    def test_no_generator_subtitle_has_a_redundant_inline_center_style(self):
        self.assertNotIn('class="generator-subtitle" style=', self.html)


class OutputPlaceholderConsistencyTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_generators_initial_placeholder_uses_the_shared_class(self):
        for expected in (
            '<p class="output-placeholder">✨ AI code output will appear here...</p>',
            '<p class="output-placeholder">Image will appear here...</p>',
            '<p class="output-placeholder">Your generated story will appear here...</p>',
            '<p class="output-placeholder">Your generated application will appear here...</p>',
            '<p class="output-placeholder">Your generated proposal will appear here...</p>',
        ):
            self.assertIn(expected, self.html)

    def test_reset_functions_also_restore_the_styled_placeholder_not_a_bare_p(self):
        # A fix that only touched initial markup but not the reset
        # button's JS would silently regress the very first time a user
        # clicked Reset -- confirm both were updated together.
        for expected in (
            "document.getElementById('storyOutput').innerHTML = '<p class=\"output-placeholder\">Your generated story will appear here...</p>';",
            "document.getElementById('applicationOutput').innerHTML = '<p class=\"output-placeholder\">Your generated application will appear here...</p>';",
            "document.getElementById('proposalOutput').innerHTML = '<p class=\"output-placeholder\">Your generated proposal will appear here...</p>';",
        ):
            self.assertIn(expected, self.html)

    def test_code_generators_own_js_string_reset_also_uses_the_shared_class(self):
        self.assertIn("<p class='output-placeholder'>✨ AI code output will appear here...</p>", self.html)

    def test_output_placeholder_class_is_actually_styled_once_shared(self):
        self.assertEqual(self.html.count(".output-placeholder {"), 1)
        start = self.html.index(".output-placeholder {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("color: var(--text-secondary)", block)
        self.assertIn("text-align: center", block)


class PreviewImageResponsiveTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_preview_image_has_an_explicit_responsive_rule(self):
        # Previously had NO CSS rule at all -- only accidentally
        # centered via a parent inline style this pass removed. A real,
        # explicit rule now covers centering AND prevents overflow.
        start = self.html.index(".preview-image {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("max-width: 100%", block)
        self.assertIn("margin: 0 auto", block)

    def test_photo_output_area_no_longer_relies_on_a_parent_inline_style(self):
        self.assertNotIn('id="generatedPreview" class="output-area" aria-live="polite" style=', self.html)
        self.assertNotIn('class="result-heading" style="text-align:center;"', self.html)


class CodeGeneratorRedundantInlineStylesRemovedTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_code_labels_no_longer_have_redundant_inline_color(self):
        self.assertIn('<label for="codeLang">Language:</label>', self.html)
        self.assertIn(
            '<label for="codePrompt">Your Requirement:<span class="required-marker" aria-hidden="true">*</span><span class="sr-only"> required</span></label>',
            self.html,
        )
        self.assertNotIn('label style="color:var(--text-primary);"', self.html)

    def test_code_actions_row_uses_a_real_class_not_an_inline_style(self):
        self.assertIn('<div class="code-actions">', self.html)
        self.assertNotIn('class="code-actions" style=', self.html)
        start = self.html.index(".code-actions {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("display: flex", block)
