"""
PHASE 6 STEP 6.5 -- Entitlement/Credit UX regression tests.

Covers:
  - the exact slug<->Feature-code mapping the frontend uses to read
    /api/entitlements/ (this was a real bug caught during development --
    a naive slug-to-slug comparison would have silently matched nothing,
    since /api/entitlements/ identifies features by the internal Feature
    code "AI_CODE"/"AI_STORY"/... not the /api/ai/ request slug
    "code"/"story"/...)
  - the entitlement banner never claims to authorize access itself --
    /api/ai/ remains the sole real authority, unconditionally re-checked
    server-side regardless of what the banner shows
  - the banner reuses the SAME BillingFlow-driven upgrade card the
    reactive credit-exhaustion failure path already uses, not a second
    billing integration
"""

from django.test import TestCase

from accounts import entitlement_config


class EntitlementApiContractTests(TestCase):
    """
    Proves the real /api/entitlements/ response shape the frontend's
    GENERATOR_SLUG_TO_ENTITLEMENT_FEATURE_CODE map depends on.
    """

    def test_entitlements_endpoint_identifies_features_by_internal_code_not_slug(self):
        resp = self.client.get("/api/entitlements/")
        self.assertEqual(resp.status_code, 200)
        feature_identifiers = {f["feature"] for f in resp.json()["features"]}
        # The exact first-column values of entitlement_config.FEATURE_DEFINITIONS
        self.assertIn("AI_CODE", feature_identifiers)
        self.assertIn("AI_STORY", feature_identifiers)
        self.assertIn("AI_APPLICATION", feature_identifiers)
        self.assertIn("AI_PROPOSAL", feature_identifiers)
        self.assertIn("AI_PHOTO", feature_identifiers)
        # The /api/ai/ request slugs must NOT appear as identifiers --
        # this is exactly the mismatch that would have made a naive
        # frontend slug comparison silently match nothing.
        self.assertNotIn("code", feature_identifiers)
        self.assertNotIn("story", feature_identifiers)

    def test_every_generator_slug_used_by_the_frontend_map_is_a_real_feature_definition(self):
        # Locks the frontend's GENERATOR_SLUG_TO_ENTITLEMENT_FEATURE_CODE
        # map (templates/main.html) to the actual source of truth --
        # if entitlement_config.FEATURE_DEFINITIONS ever changes shape,
        # this test (not just the frontend one) will catch the drift.
        expected = {
            "chat": "AI_CHAT",
            "code": "AI_CODE",
            "story": "AI_STORY",
            "application": "AI_APPLICATION",
            "proposal": "AI_PROPOSAL",
            "photo": "AI_PHOTO",
        }
        actual = {slug: code for code, slug, _name in entitlement_config.FEATURE_DEFINITIONS}
        self.assertEqual(actual, expected)


class EntitlementBannerFrontendWiringTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_banner_hook_element_present(self):
        self.assertIn('id="entitlementBanner"', self.html)

    def test_frontend_uses_the_correct_slug_to_feature_code_mapping(self):
        self.assertIn("const GENERATOR_SLUG_TO_ENTITLEMENT_FEATURE_CODE = {", self.html)
        self.assertIn('code: "AI_CODE"', self.html)
        self.assertIn('story: "AI_STORY"', self.html)
        self.assertIn('application: "AI_APPLICATION"', self.html)
        self.assertIn('proposal: "AI_PROPOSAL"', self.html)
        self.assertIn('photo: "AI_PHOTO"', self.html)

    def test_banner_fetches_the_real_entitlements_endpoint(self):
        self.assertIn('fetch("/api/entitlements/"', self.html)

    def test_banner_never_grants_access_itself_only_displays_server_decision(self):
        # The banner reads featureEntry.allowed (server's own boolean) to
        # decide whether to show at all -- it must not independently
        # compute or override that boolean.
        self.assertIn("featureEntry.allowed", self.html)
        # No client-side premium/entitlement flag is ever assigned.
        self.assertNotIn("isPremium = true", self.html)
        self.assertNotIn("entitlement = true", self.html)

    def test_banner_reuses_the_existing_billing_flow_not_a_second_one(self):
        # renderExhaustionCard() (the reactive credit-exhaustion card's
        # renderer, which drives window.BillingFlow) is called directly
        # by the proactive banner for upgrade-eligible reasons --
        # confirming there's exactly one BillingFlow integration, not two.
        self.assertIn("renderExhaustionCard(bannerEl, copy.message, copy.title)", self.html)

    def test_refreshed_on_tab_switch_and_after_successful_generation(self):
        self.assertIn("refreshEntitlementBanner();", self.html)
        # PHASE 8 STEP 10 (performance audit): the old standalone
        # `addEventListener("DOMContentLoaded", refreshEntitlementBanner)`
        # fired unconditionally AND switchTab() (also called on initial
        # load whenever a deep link is present) called it again -- two
        # /api/entitlements/ requests for the same initial banner state.
        # Removed in favor of exactly one initial call, made from inside
        # the single DOMContentLoaded handler that already exists for
        # deep-link/history-restore purposes: via switchTab() when a deep
        # link is present, or directly otherwise -- confirming both halves
        # of that mutually-exclusive branch are still present.
        self.assertIn("switchTab(initialTab, null, false);", self.html)
        self.assertIn(
            "} else if (typeof refreshEntitlementBanner === \"function\") {\n"
            "            refreshEntitlementBanner();",
            self.html,
        )
        self.assertNotIn(
            'addEventListener("DOMContentLoaded", refreshEntitlementBanner)', self.html
        )

    def test_all_seven_real_denial_reasons_have_copy(self):
        for reason in (
            "FEATURE_DISABLED",
            "PLAN_NOT_ALLOWED",
            "ENTITLEMENT_INACTIVE",
            "ENTITLEMENT_EXPIRED",
            "QUOTA_EXCEEDED",
            "GUEST_RESTRICTION",
            "AUTHENTICATION_REQUIRED",
        ):
            self.assertIn(f"{reason}: {{", self.html)
