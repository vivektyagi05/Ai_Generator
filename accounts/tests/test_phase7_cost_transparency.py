"""
PHASE 7 STEP 7.4 -- cost transparency.

New read-only GET /api/ai/costs/ endpoint. Verifies the real contract:
public (no auth required, matches /api/plans/'s own convention), GET-only,
covers exactly the five generator-facing features (chat is a sixth
registered feature but has no generator tab in main.html -- not
asserted against here, only that the five generator features are all
present), and -- most importantly -- that every number it returns comes
from the SAME accounts.credit_config.estimate_reservation_credits()
function accounts.services.ai_usage_service already calls at real
reservation time, not a second, independently-maintained formula that
could silently drift from what a request actually gets charged.
"""

from __future__ import annotations

from django.test import Client, TestCase

from accounts import credit_config


class AiCostsEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_costs_endpoint_is_public_no_auth_required(self):
        response = self.client.get("/api/ai/costs/")
        self.assertEqual(response.status_code, 200)

    def test_only_get_is_allowed(self):
        response = self.client.post("/api/ai/costs/")
        self.assertEqual(response.status_code, 405)

    def test_all_five_generator_features_are_present(self):
        body = self.client.get("/api/ai/costs/").json()
        for slug in ("code", "story", "application", "proposal", "photo"):
            self.assertIn(slug, body["costs"], f"{slug} missing from /api/ai/costs/ response")

    def test_photo_is_flat_billing_and_matches_the_real_flat_cost_constant(self):
        body = self.client.get("/api/ai/costs/").json()
        photo = body["costs"]["photo"]
        self.assertEqual(photo["billing"], "flat")
        self.assertEqual(photo["credits"], credit_config.IMAGE_GENERATION_CREDIT_COST)

    def test_token_based_features_report_a_minimum_derived_from_the_real_estimator(self):
        body = self.client.get("/api/ai/costs/").json()
        for slug in ("code", "story", "application", "proposal"):
            entry = body["costs"][slug]
            self.assertEqual(entry["billing"], "token_based")
            expected = credit_config.estimate_reservation_credits("", feature=slug)
            self.assertEqual(
                entry["minimum_credits"],
                expected,
                f"{slug}'s reported minimum_credits does not match the real "
                "estimate_reservation_credits() the reservation path uses",
            )

    def test_response_never_exposes_internal_config_names_only_public_shape(self):
        body = self.client.get("/api/ai/costs/").json()
        raw = str(body)
        for leaky_term in ("CREDIT_PER_1000_TOKENS", "FLAT_COST_FEATURES", "settings."):
            self.assertNotIn(leaky_term, raw)


class CostHintFrontendWiringTests(TestCase):
    """
    Verifies templates/main.html actually fetches the real endpoint above
    and paints it next to each Generate button -- not a hardcoded number
    duplicating what the backend already owns (RULE 11), and fetched
    exactly once per page load, not per keystroke/tab-switch (7.14
    performance: no unnecessary repeated API calls).
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_cost_hint_elements_exist_next_to_their_generate_button(self):
        for hint_id in (
            "codeCostHint",
            "photoCostHint",
            "storyCostHint",
            "applicationCostHint",
            "proposalCostHint",
        ):
            self.assertIn(f'id="{hint_id}"', self.html)

    def test_cost_hints_are_populated_from_the_real_endpoint_not_hardcoded(self):
        self.assertIn('fetch("/api/ai/costs/"', self.html)
        # The rendered hint text is built from the fetched entry's fields,
        # never a literal credit number baked into the template itself.
        self.assertIn("entry.minimum_credits", self.html)
        self.assertIn("entry.credits", self.html)

    def test_cost_endpoint_is_fetched_exactly_once_not_per_keystroke_or_tab_switch(self):
        import re

        self.assertEqual(len(re.findall(r'fetch\("/api/ai/costs/"', self.html)), 1)

    def test_cost_hint_fetch_is_registered_on_domcontentloaded_not_repeated_elsewhere(self):
        self.assertIn('document.addEventListener("DOMContentLoaded", loadGeneratorCostHints)', self.html)
