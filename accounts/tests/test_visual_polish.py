"""
PHASE 6 STEP 6.8F -- workspace visual polish, concrete implemented fixes.

Covers:
  - a real button-consistency bug: copyCodeBtn/downloadCodeBtn were
    missing the base .btn class, which a duplicate, slightly-different
    .btn-primary/.btn-secondary declaration existed to compensate for --
    two competing sources of truth for the same button look. Fixed at
    the source (both buttons now use the canonical .btn + .btn-primary/
    .btn-secondary combo like every other button), duplicate removed.
  - dead CSS from the 6.8A header removal (.header, .logo, .logo-text,
    .auth-buttons, .welcome-text) that was missed at the time.
  - a genuine tablet-tier breakpoint (768-1199px) that didn't exist
    before -- tablets got the exact same fixed 300px sidebar as desktop.
  - a narrow-mobile tier for button sizing.

No browser was run this batch (see PHASE_6_WORKSPACE_UI_AUDIT.md) --
these tests verify the CSS/HTML implementation is actually present and
internally consistent, which is everything checkable without one.
"""

from django.test import TestCase


class ButtonConsistencyTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_code_action_buttons_use_the_canonical_btn_class(self):
        self.assertIn('class="btn btn-primary" id="copyCodeBtn"', self.html)
        self.assertIn('class="btn btn-secondary" id="downloadCodeBtn"', self.html)

    def test_no_button_uses_btn_primary_or_secondary_without_the_base_btn_class(self):
        import re

        # Any class="..." whose class LIST contains the exact token
        # "btn-primary" or "btn-secondary" (not e.g. nav_app.html's
        # unrelated "ds-btn-secondary") must also contain the exact
        # token "btn".
        for m in re.finditer(r'class="([^"]*)"', self.html):
            classes = m.group(1).split()
            if "btn-primary" in classes or "btn-secondary" in classes:
                self.assertIn("btn", classes, f"button missing base .btn class: class=\"{m.group(1)}\"")

    def test_btn_primary_and_secondary_are_each_defined_exactly_once(self):
        # The duplicate global redeclaration (with slightly different
        # gradient colors and missing position/overflow context for the
        # shimmer effect) is gone -- one canonical definition each.
        import re

        css_start = self.html.index("<style>")
        css_end = self.html.index("</style>")
        css = self.html[css_start:css_end]
        primary_defs = re.findall(r'(?:^|\})\s*\.btn-primary\s*\{', css)
        secondary_defs = re.findall(r'(?:^|\})\s*\.btn-secondary\s*\{', css)
        self.assertEqual(len(primary_defs), 1, "‌.btn-primary should be defined exactly once")
        self.assertEqual(len(secondary_defs), 1, ".btn-secondary should be defined exactly once")


class DeadCssRemovedTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_dead_header_related_css_selectors_are_gone(self):
        import re

        css_start = self.html.index("<style>")
        css_end = self.html.index("</style>")
        css = self.html[css_start:css_end]
        for dead_selector in (r"\.header\s*\{", r"\.logo\s*\{", r"\.logo-text\s*\{", r"\.auth-buttons\s*\{", r"\.welcome-text\s*\{"):
            self.assertNotRegex(css, dead_selector, f"dead CSS selector still present: {dead_selector}")

    def test_no_element_in_the_page_references_the_removed_classes(self):
        for dead_class in ("logo-text", "welcome-text"):
            self.assertNotIn(f'class="{dead_class}"', self.html)


class ResponsiveBreakpointTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_tablet_tier_breakpoint_exists(self):
        self.assertIn("@media (min-width: 768px) and (max-width: 1199px)", self.html)

    def test_mobile_tier_breakpoint_still_exists(self):
        self.assertIn("@media (max-width: 768px)", self.html)

    def test_narrow_mobile_tier_breakpoint_exists(self):
        self.assertIn("@media (max-width: 420px)", self.html)

    def test_modal_content_dead_css_was_removed_not_carried_forward(self):
        # PHASE 7 GOLDEN UI: .modal-content (and .modal, .upload-area,
        # .dragover, .preview-container, .preview-box, .input-group,
        # .close, .body-grid) matched no element anywhere in this
        # template's rendered markup or its own JS (confirmed by
        # grepping the actual DOM/JS strings, not assumed) -- the one
        # real querySelectorAll('.modal') call in this file's JS finds
        # nothing to close because no element ever carries that class.
        # A max-width safety net on a rule nothing uses isn't a fix, so
        # this batch removed the dead selector instead of re-carrying
        # it forward with a patched value.
        css_start = self.html.index("<style>")
        css_end = self.html.index("</style>")
        css = self.html[css_start:css_end]
        self.assertNotIn(".modal-content {", css)
