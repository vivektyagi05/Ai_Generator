"""
PHASE 6 STEP 6.9 -- accessibility, code-level verification.

No browser or screen reader was run this batch -- these tests verify
what's actually checkable without one: label/input association (the
concrete thing a screen reader needs to announce "Story Topic, edit
text", not just "edit text"), aria-live regions on the five async result
containers plus the entitlement banner, and aria-current kept in sync
with the visually-active generator tab. Real device/screen-reader
verification remains explicitly external -- see
PHASE_6_ACCESSIBILITY_AUDIT.md.
"""

import re

from django.test import TestCase


class LabelInputAssociationTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_every_label_has_a_for_attribute(self):
        labels = re.findall(r"<label\b[^>]*>", self.html)
        self.assertGreater(len(labels), 0, "expected to find generator input labels")
        unassociated = [l for l in labels if 'for="' not in l]
        self.assertEqual(unassociated, [], f"labels missing for=: {unassociated}")

    def test_every_label_for_target_actually_exists(self):
        # A for="nonexistent-id" is worse than no label at all -- it tells
        # assistive tech there's an association and then silently fails to
        # deliver one. Every target id must be a real element on the page.
        for_targets = re.findall(r'<label\b[^>]*\bfor="([^"]+)"', self.html)
        self.assertGreater(len(for_targets), 0)
        for target_id in for_targets:
            self.assertIn(f'id="{target_id}"', self.html, f"label for={target_id!r} has no matching element")

    def test_all_five_generators_have_labeled_primary_input(self):
        expected = {
            "codePrompt": "Your Requirement",
            "imagePrompt": "Describe your image",
            "storyTopic": "Story Topic",
            "applicantName": "Your Name",
            "proposalTitle": "Proposal Title",
        }
        for input_id, label_text in expected.items():
            self.assertIn(f'for="{input_id}"', self.html)


class AriaLiveRegionTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_result_containers_have_aria_live(self):
        for output_id in (
            "codeOutput", "generatedPreview", "storyOutput",
            "applicationOutput", "proposalOutput",
        ):
            # Match the element regardless of attribute order.
            pattern = re.compile(rf'id="{output_id}"[^>]*aria-live="polite"|aria-live="polite"[^>]*id="{output_id}"')
            self.assertRegex(self.html, pattern, f"{output_id} missing aria-live")

    def test_entitlement_banner_has_aria_live(self):
        self.assertRegex(
            self.html,
            r'id="entitlementBanner"[^>]*aria-live="polite"',
        )


class GeneratorTabAccessibleStateTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_nav_tabs_are_real_buttons_not_clickable_divs(self):
        # Semantic requirement: focusable and activatable via Enter/Space
        # natively, no custom keyboard handling needed.
        for tab_id in ("photo", "story", "application", "proposal", "codecreator"):
            self.assertRegex(self.html, rf'<button class="nav-tab[^"]*" data-tab="{tab_id}"')

    def test_default_active_tab_has_aria_current_true(self):
        self.assertIn('data-tab="photo" aria-current="true"', self.html)

    def test_inactive_tabs_have_aria_current_false(self):
        for tab_id in ("story", "application", "proposal", "codecreator"):
            self.assertIn(f'data-tab="{tab_id}" aria-current="false"', self.html)

    def test_switchTab_keeps_aria_current_in_sync_with_active_class(self):
        # Behavioral requirement, not just presence: the same function
        # call that toggles the visual .active class must also toggle
        # aria-current, so a screen reader and a sighted user never
        # disagree about which generator is selected.
        self.assertIn("b.setAttribute('aria-current', 'false');", self.html)
        self.assertIn("targetBtn.setAttribute('aria-current', 'true');", self.html)
