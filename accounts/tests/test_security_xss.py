"""
PHASE 6 STEP 6.10 -- security sweep findings.

REAL FINDING, not a hypothetical: generateStory()/generateApplication()/
generateProposal()'s onSuccess handlers inserted their AI-generated
result text into the DOM via innerHTML WITHOUT escapeHtml() -- unlike
generateCode(), which already correctly used escapeHtml(codeOnly). An
LLM response containing markup (asked for directly, e.g. "write a story
that includes this HTML: <script>...</script>", or produced by the model
unprompted) would have executed as real script in the browser on the
result page for three of the five generators. Found by reading every
`.innerHTML =` assignment in templates/main.html (24 total) individually
rather than trusting the presence of escapeHtml() being used ANYWHERE in
the file as evidence it was used EVERYWHERE it needed to be.

Since this is pure client-side JS with no DOM to execute it in a Django
test, these tests assert the exact fix is present in the rendered
source -- the same limitation (and the same escapeHtml() call already
proven correct for Code) as every other frontend-contract test in this
suite.
"""

from django.test import TestCase


class ResultRenderingXssTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_story_result_is_escaped(self):
        # PHASE 7 GOLDEN UI STEP F: normalized from <div> to <pre>
        # (functionally identical -- .pretty-output's CSS already forced
        # white-space: pre-wrap regardless of tag -- but <pre> is the
        # semantically correct element and now matches Code/Application/
        # Proposal). The escaping itself (the actual security property
        # this test exists to lock in) is unchanged.
        self.assertIn('<pre class="pretty-output">${escapeHtml(storyText)}</pre>', self.html)

    def test_application_result_is_escaped(self):
        self.assertIn(
            '<pre class="pretty-output">${escapeHtml(applicationText)}</pre>', self.html
        )

    def test_proposal_result_is_escaped(self):
        self.assertIn(
            '<pre class="pretty-output">${escapeHtml(proposalText)}</pre>', self.html
        )

    def test_code_result_was_already_escaped_unchanged(self):
        # Confirms the fix matches Code's pre-existing correct pattern
        # rather than introducing a new one, and that this batch didn't
        # accidentally touch the one generator that was already right.
        self.assertIn(
            '<pre class="pretty-output" id="generatedCode">${escapeHtml(codeOnly)}</pre>', self.html
        )
        self.assertIn("${escapeHtml(explanation).replace(", self.html)

    def test_all_four_text_generators_now_use_the_same_pre_wrapper_element(self):
        # PHASE 7 GOLDEN UI STEP F: Story was the one holdout using <div>
        # instead of <pre> for .pretty-output. All four (Code/Story/
        # Application/Proposal) now agree on the same semantic element.
        import re

        self.assertEqual(len(re.findall(r'<pre class="pretty-output"', self.html)), 4)
        self.assertNotIn('<div class="pretty-output">', self.html)

    def test_no_generator_result_still_interpolates_raw_ai_text_into_innerhtml(self):
        # Structural guard against regressing back to the bug: none of the
        # four raw result variable names may appear inside a template
        # literal's ${...} interpolation without escapeHtml() wrapping it.
        import re

        for var_name in ("storyText", "applicationText", "proposalText", "codeOnly"):
            # Find every `${...}` interpolation that references this exact
            # variable name (word-boundary) anywhere in an innerHTML
            # template literal, and confirm every single one is wrapped in
            # escapeHtml(...).
            raw_uses = re.findall(rf"\$\{{[^}}]*\b{var_name}\b[^}}]*\}}", self.html)
            self.assertGreater(len(raw_uses), 0, f"expected at least one ${{...}} use of {var_name}")
            for use in raw_uses:
                self.assertIn(
                    "escapeHtml(", use, f"{var_name} interpolated without escapeHtml(): {use}"
                )
