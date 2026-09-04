"""
PHASE 5 STEP 3 / PHASE_2_FRONTEND_GAP_MATRIX.md GAP-04 regression test.

Before this change, /credits/balance/ had no frontend caller anywhere
except templates/profile.html -- a user spending credits on the
generator page (/home/) had no visibility into their remaining balance
until navigating away. This asserts the fix is actually present on the
rendered page: the shared DS.credits widget (static/js/credits.js, the
same Phase 2 foundation component, not a second implementation) is
loaded and mounted against a real hook element, and refreshed after each
of the five generators' successful paths (not just on initial page load,
since credits are only ever debited server-side after success).
"""

from django.test import TestCase


class CreditPillWiringTests(TestCase):
    def setUp(self):
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 200)
        self.html = response.content.decode()

    def test_shared_credit_widget_scripts_are_loaded(self):
        self.assertIn('src="/static/js/api.js"', self.html)
        self.assertIn('src="/static/js/credits.js"', self.html)

    def test_credit_pill_hook_element_present(self):
        self.assertIn("data-ds-credit-pill", self.html)

    def test_mounted_on_initial_load(self):
        self.assertIn("refreshCreditPill", self.html)
        self.assertIn('addEventListener("DOMContentLoaded", refreshCreditPill)', self.html)

    def test_refreshed_after_every_generator_success(self):
        # PHASE 6 STEP 6.2 update: all five generators now route through
        # the shared runGeneratorLifecycle() controller (see
        # PHASE_6_WORKSPACE_ARCHITECTURE.md), which calls refreshCreditPill()
        # itself exactly once, rather than each generator carrying its own
        # copy of the call. The invariant this test protects -- credits
        # refresh after every successful generation, not just initial load
        # -- still holds; it's proven by (a) exactly one such call existing
        # in the whole page (inside the controller), and (b) every
        # generator actually being wired through that controller (checked
        # per-generator in test_generator_lifecycle.py). Asserting "6
        # separate call-sites" here would just re-encode the old,
        # deliberately-removed duplication as a test requirement.
        self.assertEqual(self.html.count("refreshCreditPill();"), 1)
        self.assertIn('addEventListener("DOMContentLoaded", refreshCreditPill)', self.html)
