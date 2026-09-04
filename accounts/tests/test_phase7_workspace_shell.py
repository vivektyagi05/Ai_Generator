"""
PHASE 7 GOLDEN UI STEP A (workspace layout) -- real structural
inconsistency found: Photo/Story/Application/Proposal all used a shared
`.main-content > .editor-section + .sidebar` two-column workspace shell,
but Code was laid out as flat single-column markup with its "Quick
Actions" (Reset) sitting inline mid-form instead of in the same sidebar
position every other generator uses. Fixed by moving Code's existing
elements (same ids, same onclick handlers, same generateCode(event) /
resetCodeBtn wiring -- nothing behavioral changed) into the same shell.

Verifies the restructure preserved every functional contract: same
element ids, same onclick handlers, same descendant CSS selectors still
matching (all of #codecreator-tab's CSS rules in this file are
descendant selectors like "#codecreator-tab textarea", which still
match regardless of the new intermediate wrapper divs).
"""

from django.test import TestCase


class CodeGeneratorSharesTheWorkspaceShellTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_code_tab_now_uses_the_same_shell_as_the_other_four_generators(self):
        start = self.html.index('id="codecreator-tab"')
        end = self.html.index('id="photo-tab"')
        block = self.html[start:end]
        self.assertIn('class="main-content"', block)
        self.assertIn('class="editor-section"', block)
        self.assertIn('class="sidebar"', block)

    def test_code_generator_still_has_all_its_original_working_ids(self):
        for element_id in ("codeLang", "codePrompt", "codeOutput", "resetCodeBtn", "codeCostHint"):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_code_generate_button_still_wired_to_the_same_handler(self):
        self.assertIn('onclick="generateCode(event)"', self.html)

    def test_reset_button_is_still_in_the_sidebar_quick_actions_not_mid_form(self):
        start = self.html.index('id="codecreator-tab"')
        end = self.html.index('id="photo-tab"')
        block = self.html[start:end]
        sidebar_start = block.index('class="sidebar"')
        reset_pos = block.index('id="resetCodeBtn"')
        self.assertGreater(
            reset_pos,
            sidebar_start,
            "resetCodeBtn should now live inside .sidebar like every other generator's Quick Actions",
        )

    def test_all_five_generators_now_share_the_same_workspace_shell(self):
        import re

        self.assertEqual(len(re.findall(r'class="main-content"', self.html)), 5)
        self.assertEqual(len(re.findall(r'class="editor-section"', self.html)), 5)
        self.assertEqual(len(re.findall(r'class="sidebar"', self.html)), 5)
