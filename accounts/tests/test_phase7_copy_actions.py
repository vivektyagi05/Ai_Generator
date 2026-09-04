"""
PHASE 7 STEP 7.6/7.7 -- Copy action added to Story/Application/Proposal,
matching the one Code already had (copyCodeBtn). Follows the exact
verification pattern accounts/tests/test_visual_polish.py and
accounts/tests/test_no_dead_ui.py already established for this file:
Django's test client renders the template server-side and inspects the
emitted HTML/JS text -- it does not execute JavaScript (no browser engine
available in this environment). So "the button copies to the clipboard"
is verified as "the button exists, uses the canonical button class, is
wired to a real module-scope function that calls the existing
copyTextToClipboard() helper and the existing DS.toast infrastructure,
not a fake handler" -- not as an actual executed click. Real clipboard
behavior in a live browser is out of reach here (per
PHASE_4B_MANUAL_E2E_CHECKLIST.md's own documented limitation) and is not
claimed as verified.
"""

from django.test import TestCase


class CopyActionButtonsExistTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_story_has_a_real_copy_button_using_the_canonical_btn_class(self):
        self.assertIn('class="btn btn-secondary" id="copyStoryBtn" onclick="copyStory()"', self.html)

    def test_application_has_a_real_copy_button_using_the_canonical_btn_class(self):
        self.assertIn(
            'class="btn btn-secondary" id="copyApplicationBtn" onclick="copyApplication()"', self.html
        )

    def test_proposal_has_a_real_copy_button_using_the_canonical_btn_class(self):
        self.assertIn(
            'class="btn btn-secondary" id="copyProposalBtn" onclick="copyProposal()"', self.html
        )


class CopyActionFunctionsAreRealModuleScopeDeclarationsTests(TestCase):
    """
    Same structural check test_no_dead_ui.py's
    GENERATOR_ACTION_FUNCTIONS_MUST_FOLLOW dict runs for downloadX/shareX/
    resetX: each new copyX() must be declared strictly AFTER its
    generator's own async function opens -- i.e. at module scope, not
    nested inside the generator closure where an inline onclick=
    couldn't reach it (the exact bug class test_no_dead_ui.py exists to
    catch).
    """

    GENERATOR_DECLARATIONS = {
        "copyStory": "async function generateStory(event) {",
        "copyApplication": "async function generateApplication(event) {",
        "copyProposal": "async function generateProposal(event) {",
    }

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_copy_functions_declared_after_their_generator_not_nested_inside_it(self):
        for fn_name, generator_decl in self.GENERATOR_DECLARATIONS.items():
            generator_pos = self.html.index(generator_decl)
            fn_pos = self.html.index(f"function {fn_name}(")
            self.assertGreater(
                fn_pos,
                generator_pos,
                f"{fn_name}() must be declared after {generator_decl!r}",
            )

    def test_every_new_onclick_referenced_copy_function_is_declared(self):
        # Belt-and-suspenders with test_no_dead_ui.py's generic scan --
        # this locks in specifically the three functions this batch added.
        for fn_name in self.GENERATOR_DECLARATIONS:
            self.assertIn(f'onclick="{fn_name}()"', self.html)
            self.assertIn(f"function {fn_name}(", self.html)


class CopyActionsReuseExistingInfrastructureNotADuplicateTests(TestCase):
    """
    RULE 5/9/10 (no duplicate architecture): the new copy functions must
    call the SAME copyTextToClipboard() helper Code's copy button already
    used, not a second clipboard-writing implementation, and must use the
    existing DS.toast infrastructure (static/js/toast.js, loaded on this
    page since PHASE 2 but unwired until this batch) rather than a new
    notification system.
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_copy_text_to_clipboard_helper_is_still_defined_exactly_once(self):
        import re

        defs = re.findall(r"function copyTextToClipboard\(", self.html)
        self.assertEqual(len(defs), 1, "copyTextToClipboard should have exactly one definition")

    def test_new_copy_functions_call_the_existing_helper_not_navigator_clipboard_directly(self):
        import re

        for fn_name in ("copyStory", "copyApplication", "copyProposal"):
            start = self.html.index(f"function {fn_name}(")
            end = self.html.index("\n            }", start)
            body = self.html[start:end]
            self.assertIn("copyTextToClipboard(", body)
            self.assertNotIn("navigator.clipboard", body)

    def test_toast_js_is_loaded_on_this_page(self):
        self.assertIn("toast.js", self.html)

    def test_new_copy_functions_use_ds_toast_for_feedback(self):
        for fn_name in ("copyStory", "copyApplication", "copyProposal"):
            start = self.html.index(f"function {fn_name}(")
            end = self.html.index("\n            }", start)
            body = self.html[start:end]
            self.assertIn("DS.toast", body)
