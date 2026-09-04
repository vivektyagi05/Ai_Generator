"""
PHASE 7 GOLDEN UI: plans.html migrated onto the shared app shell
(base_app.html -> nav_app.html) instead of a standalone document with
its own minimal top-nav (back-to-Generators link + a single
Profile/Login link -- no History link, no credit pill, no active-page
indicator). Unlike profile.html/history.html this page must keep
working for BOTH anonymous and authenticated visitors -- covered
explicitly below since that's exactly the behavior most likely to
regress in a shell migration (base_app.html's csrf meta tag and
nav_app.html's whole auth branch are both conditional on
user.is_authenticated).
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class PlansSharedShellAnonymousTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/plans/").content.decode()

    def test_status_ok_and_uses_the_shared_nav(self):
        self.assertIn('<nav class="ds-nav">', self.html)

    def test_shared_nav_offers_login_and_signup_for_a_guest(self):
        self.assertIn(">Login<", self.html)
        self.assertIn(">Sign Up<", self.html)
        # The old page-local nav only ever offered a "Log in" link, no
        # Sign Up link and no way to reach History/Pricing from outside.
        self.assertIn(">Pricing<", self.html)

    def test_plans_nav_link_marked_current(self):
        self.assertIn('aria-current="page">Pricing<', self.html)

    def test_billing_page_content_still_renders(self):
        self.assertIn('class="billing-page"', self.html)
        self.assertIn('id="billingStatusBanner"', self.html)
        self.assertIn('class="plan-grid"', self.html)

    def test_no_csrf_meta_tag_for_a_guest(self):
        # base_app.html only emits the csrf meta tag for authenticated
        # users -- unauthenticated requests must not get a stray token.
        self.assertNotIn('name="csrf-token"', self.html)


class PlansSharedShellAuthenticatedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="plansshelluser", email="plansshell@example.com", password="pass12345"
        )
        self.client.force_login(self.user)
        self.html = self.client.get("/plans/").content.decode()

    def test_shared_nav_offers_profile_and_logout(self):
        self.assertIn(">Logout<", self.html)
        self.assertIn(">Profile<", self.html)

    def test_csrf_meta_tag_present_for_an_authenticated_user(self):
        self.assertIn('name="csrf-token"', self.html)

    def test_billing_js_still_wired(self):
        self.assertIn('src="/static/js/billing.js"', self.html)
        self.assertIn("BillingFlow", self.html)
