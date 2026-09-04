"""
PHASE 7 GOLDEN UI STEP F (visual design system) -- spacing/radius scale
for the generator workspace panels.

UPDATED this session: the earlier version of this file locked in a
*local* alias (--panel-radius: 25px, defined only inside main.html's own
<style> block) that duplicated a value already present in the shared
static/css/tokens.css (--radius-lg) under a different name and a
different number -- two sources of truth for "how rounded is a panel".
The Golden UI re-theme (see PHASE_7_GOLDEN_UI_AUDIT.md) retired the
page-local alias and switched .editor-section/.sidebar to reference the
shared --radius-lg/--shadow-sm tokens directly, so every card-like
surface on every page (this workspace, profile, plans, history) now
comes from the exact same scale instead of two.

--panel-padding-primary/--panel-padding-secondary are kept as page-local
aliases (still real behavior worth locking in: primary panel gets more
breathing room than the sidebar) but now resolve to the shared spacing
scale (--space-8/--space-5) instead of hardcoded pixel values, so they
move if the shared scale ever does.
"""

from django.test import TestCase


class SpacingTokenScaleTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_panel_padding_aliases_resolve_to_the_shared_spacing_scale(self):
        root_start = self.html.index(":root {")
        root_end = self.html.index("}", root_start)
        root_block = self.html[root_start:root_end]
        self.assertIn("--panel-padding-primary: var(--space-8);", root_block)
        self.assertIn("--panel-padding-secondary: var(--space-5);", root_block)
        # The old page-local radius/transition aliases are gone -- panels
        # reference the shared tokens directly now (see tests below).
        self.assertNotIn("--panel-radius:", root_block)

    def test_editor_section_uses_the_shared_radius_and_shadow_tokens(self):
        start = self.html.index(".editor-section {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("padding: var(--panel-padding-primary);", block)
        self.assertIn("border-radius: var(--radius-lg);", block)
        self.assertIn("box-shadow: var(--shadow-sm);", block)
        self.assertNotIn("padding: 30px;", block)

    def test_sidebar_uses_the_shared_radius_and_shadow_tokens(self):
        start = self.html.index(".sidebar {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("padding: var(--panel-padding-secondary);", block)
        self.assertIn("border-radius: var(--radius-lg);", block)
        self.assertIn("box-shadow: var(--shadow-sm);", block)
        self.assertNotIn("padding: 20px;", block)

    def test_root_block_defined_exactly_once(self):
        import re

        self.assertEqual(len(re.findall(r":root\s*\{", self.html)), 1)
