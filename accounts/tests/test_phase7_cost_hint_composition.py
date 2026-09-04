"""
PHASE 7 Golden UI visual composition pass (structural verification only
-- no browser available in this environment, same constraint noted
throughout this checkpoint): `.generator-cost-hint` previously had no
visual identity of its own -- 5 identical, duplicated inline
`style="color:var(--text-secondary); font-size:0.85em; margin-top:6px;"`
attributes, one per generator, rendering as plain small gray caption
text under the generate button. Given a real badge/chip treatment
(icon, background, border) instead, with a distinct visually-different
state for "generating" -- driven entirely by the existing
`data-status="generating"` attribute (no JS behavior changed).
"""

from django.test import TestCase


class CostHintBadgeCompositionTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_duplicated_inline_style_removed_from_all_five_generators(self):
        self.assertEqual(
            self.html.count('style="color:var(--text-secondary); font-size:0.85em; margin-top:6px;"'),
            0,
        )
        for gen_id in (
            "codeCostHint", "photoCostHint", "storyCostHint",
            "applicationCostHint", "proposalCostHint",
        ):
            self.assertIn(f'<p class="generator-cost-hint" id="{gen_id}" aria-live="polite"></p>', self.html)

    def test_base_cost_hint_has_a_real_badge_treatment_not_bare_text(self):
        start = self.html.index(".generator-cost-hint {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("border-radius: var(--radius-pill)", block)
        self.assertIn("background: var(--bg-main)", block)
        self.assertIn("border: 1px solid var(--border-color)", block)

    def test_empty_hint_is_hidden_not_shown_as_an_empty_chip(self):
        self.assertIn(".generator-cost-hint:empty { display: none; }", self.html)

    def test_generating_state_is_visually_distinct_from_the_base_chip(self):
        start = self.html.index('.generator-cost-hint[data-status="generating"] {')
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("var(--info-bg)", block)
        self.assertIn("var(--primary-color)", block)

    def test_generating_state_does_not_double_up_the_icon(self):
        # setGeneratorStatusBadge() sets textContent to its own leading
        # icon ("Generating..." text prefixed with an hourglass emoji)
        # -- the CSS ::before icon must be suppressed for this state so
        # the two don't stack.
        self.assertIn('el.textContent = "\u23f3 Generating\u2026";', self.html)
        generating_before_start = self.html.index('.generator-cost-hint[data-status="generating"]::before')
        generating_before_end = self.html.index("}", generating_before_start)
        block = self.html[generating_before_start:generating_before_end]
        self.assertIn("content: none;", block)


class ResultPanelHierarchyTests(TestCase):
    """
    PHASE 7 Golden UI visual composition, panel-level refinement: the
    input area and result area previously sat in one continuous flat
    flow inside .editor-section with no visual break -- the "Result"
    heading was just another item in the same vertical list as the form
    labels above it. A top divider on the shared .result-heading
    selector (used by all 5 generators) gives the panel real internal
    hierarchy without any HTML/markup changes.
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_result_heading_has_a_visual_divider_separating_it_from_the_input_area(self):
        start = self.html.index(".result-heading {")
        end = self.html.index("}", start)
        block = self.html[start:end]
        self.assertIn("border-top: 1px solid var(--border-color)", block)
        self.assertIn("padding-top: var(--space-5)", block)

    def test_result_heading_rule_is_shared_not_duplicated_per_generator(self):
        # Confirms this is one rule applying to all 5 generators' result
        # headings, not 5 separate copies that could drift.
        self.assertEqual(self.html.count(".result-heading {"), 1)
        for result_h3_class in ('<h3 class="result-heading"',):
            self.assertGreaterEqual(self.html.count(result_h3_class), 5)
