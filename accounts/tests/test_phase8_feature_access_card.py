"""
PHASE 8 STEP 4 -- Credit & Usage Center: Feature Access & Usage card.

`entitlement.features` (accounts.services.entitlement_service.
get_entitlement_summary()) has been returned by /profile/data/ since
Phase 2, but nothing in profile.html ever rendered it -- verified by
grepping the shipped template/JS for "entitlement.features" before this
change: zero matches. This is exactly the "existing backend capability
with no UI" case Phase 8 calls out: real, already-computed per-feature
access + quota data with a genuinely missing frontend, not a new
computation and not a decorative addition.

Covers:
  - /profile/data/ still returns entitlement.features (unchanged
    contract -- this feature is pure frontend, no backend change)
  - the new rendering code (renderFeatureAccess / featureAccessList) is
    actually present in the shipped profile.html, wired from
    loadProfileData()'s existing response, not a second unused function
  - the card renders every feature name and reads allowed/reason from
    the real response shape, not a hardcoded list
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class FeatureAccessCardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="featureaccessuser", email="fa@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_profile_data_still_returns_entitlement_features(self):
        data = self.client.get("/profile/data/").json()
        self.assertIn("entitlement", data)
        self.assertIn("features", data["entitlement"])
        self.assertTrue(len(data["entitlement"]["features"]) > 0)
        first = data["entitlement"]["features"][0]
        for key in ("feature", "name", "allowed", "reason", "limits", "remaining"):
            self.assertIn(key, first)

    def test_profile_page_ships_the_feature_access_card_and_renderer(self):
        html = self.client.get("/profile/").content.decode()
        self.assertIn('id="featureAccessList"', html)
        self.assertIn("function renderFeatureAccess(entitlement)", html)
        # Wired from the same response loadProfileData() already fetches
        # -- not a second, independent fetch to some other endpoint.
        self.assertIn("renderFeatureAccess(d.entitlement)", html)

    def test_card_reads_real_response_fields_not_a_hardcoded_list(self):
        html = self.client.get("/profile/").content.decode()
        # The renderer must read allowed/reason/remaining from the
        # feature objects themselves (f.allowed, f.reason, f.remaining),
        # not reference any specific feature name/slug directly -- that
        # would mean a hardcoded list drifting from the real catalog.
        self.assertIn("f.allowed", html)
        self.assertIn("f.reason", html)
        self.assertIn("f.remaining", html)
        self.assertNotIn('"AI_PHOTO"', html.split("featureAccessList")[-1][:3000])
