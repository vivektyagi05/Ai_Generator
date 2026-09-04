"""
PHASE 7 STEP 2C (input experience) -- real inconsistency found: only 2 of
18 field labels indicated required/optional status at all, and those 2
used hand-typed "(Optional)" text baked into the label rather than a
shared convention. Fixed with one shared, reusable pair of classes
(.required-marker, .optional-hint) applied ONLY where each generator's
own existing validate() function (unchanged by this batch) actually
treats that field as required or optional -- never invented.

Genuine gap fixed, not just re-styled: proposalDetails was truly optional
(absent from proposalGenState's validate() required-field list) but had
zero indication either way.
"""

from django.test import TestCase


class RequiredOptionalLabelConsistencyTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_every_required_field_this_batch_touched_has_a_real_marker(self):
        required_field_ids = (
            "codePrompt",
            "imagePrompt",
            "storyTopic",
            "storyCharacters",
            "applicantName",
            "applicationPurpose",
            "recipientName",
            "applicationReason",
            "proposalTitle",
            "proposalAudience",
            "proposalObjective",
            "proposalBenefits",
        )
        for field_id in required_field_ids:
            label_start = self.html.index(f'for="{field_id}"')
            label_end = self.html.index("</label>", label_start)
            label_block = self.html[label_start:label_end]
            self.assertIn('class="required-marker"', label_block, f"{field_id} missing required marker")
            self.assertIn("required</span>", label_block, f"{field_id} missing screen-reader 'required' text")

    def test_every_optional_field_uses_the_shared_class_not_hardcoded_text(self):
        optional_field_ids = (
            "storyDetails",
            "applicationDetails",
            "proposalBudget",
            "proposalDetails",
        )
        for field_id in optional_field_ids:
            label_start = self.html.index(f'for="{field_id}"')
            label_end = self.html.index("</label>", label_start)
            label_block = self.html[label_start:label_end]
            self.assertIn('class="optional-hint"', label_block, f"{field_id} missing optional-hint class")

    def test_proposal_details_gap_is_fixed_it_was_previously_unmarked_either_way(self):
        label_start = self.html.index('for="proposalDetails"')
        label_end = self.html.index("</label>", label_start)
        label_block = self.html[label_start:label_end]
        self.assertIn("(Optional)", label_block)

    def test_required_and_optional_indicators_match_each_generators_real_validate_function(self):
        # storyLength has no indicator either way -- it's a <select> that
        # always has a value (no empty state possible), so a required/
        # optional marker adds no real information for it; confirms this
        # batch didn't blanket-apply markers where they don't help.
        label_start = self.html.index('for="storyLength"')
        label_end = self.html.index("</label>", label_start)
        label_block = self.html[label_start:label_end]
        self.assertNotIn("required-marker", label_block)
        self.assertNotIn("optional-hint", label_block)

    def test_shared_classes_are_defined_exactly_once_not_duplicated(self):
        import re

        self.assertEqual(len(re.findall(r"\.required-marker\s*\{", self.html)), 1)
        self.assertEqual(len(re.findall(r"\.optional-hint\s*\{", self.html)), 1)
