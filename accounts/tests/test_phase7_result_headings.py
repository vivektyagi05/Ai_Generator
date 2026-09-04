"""
PHASE 7 GOLDEN UI STEP C (result experience) -- real gap: none of the
five output areas (codeOutput, generatedPreview, storyOutput,
applicationOutput, proposalOutput) had any heading marking where the
result section begins; the only cue was the placeholder text INSIDE the
empty div, which disappears the moment real content replaces it. Added
one shared "Result" heading (`.result-heading`, matching the existing
`.body-options h3` visual weight for "Quick Actions") as a sibling
immediately before each output div.

Verifies the heading is a SIBLING of the output div, not a child --
because every reset*()/generateCode() function replaces the div's own
`.innerHTML`, a heading placed inside the div would be wiped out on the
very first reset or regenerate.
"""

from django.test import TestCase


class ResultHeadingConsistencyTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_output_areas_now_have_a_result_heading_immediately_before_them(self):
        for output_id in (
            "codeOutput",
            "generatedPreview",
            "storyOutput",
            "applicationOutput",
            "proposalOutput",
        ):
            output_pos = self.html.index(f'id="{output_id}"')
            preceding = self.html[:output_pos]
            heading_pos = preceding.rindex('<h3 class="result-heading"')
            # The heading must be the LAST thing before this specific
            # output div (i.e. immediately precedes it, not some earlier
            # generator's heading bleeding through).
            between = preceding[heading_pos:]
            self.assertNotIn("</div>\n\n                <div class=\"sidebar\">", between)

    def test_result_heading_is_a_sibling_not_a_child_of_the_output_div(self):
        # If it were inside, e.g. resetStory()'s
        # storyOutput.innerHTML = '<p>...</p>' would delete it on first reset.
        for output_id, reset_fn in (
            ("storyOutput", "function resetStory("),
            ("applicationOutput", "function resetApplication("),
            ("proposalOutput", "function resetProposal("),
        ):
            fn_start = self.html.index(reset_fn)
            fn_end = self.html.index("\n            }", fn_start)
            fn_body = self.html[fn_start:fn_end]
            self.assertIn(f"{output_id}').innerHTML =", fn_body)
            # The reset only ever assigns a <p>, never re-adds an <h3> --
            # confirming the heading lives outside what gets replaced.
            self.assertNotIn("result-heading", fn_body)

    def test_result_heading_uses_one_shared_class_not_five_one_off_styles(self):
        import re

        self.assertEqual(len(re.findall(r'class="result-heading', self.html)), 5)
