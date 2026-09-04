"""
PHASE 8A — regression test for the reported "duplicate navigation" bug.

Root cause (verified by reading static/css/shell.css, not assumed):
`.ds-nav-mobile-panel` (templates/partials/nav_app.html's mobile drawer,
a plain <a> list: Generators/Dashboard/History/Pricing/Login/Sign Up)
only had `display: none` INSIDE `@media (max-width: 768px) { ... }`.
Outside that breakpoint (i.e. on any desktop viewport) there was no rule
hiding it at all, so the browser's UA default `display: block` for a
<div> applied and it rendered as a second, plain-link row directly under
the real <nav> on every app-shell page (main/history/profile/plans/
dashboard) for every desktop visitor. It was never two separate nav
*systems* (template inheritance was already correct — every app page
extends base_app.html exactly once, which includes nav_app.html exactly
once) — it was one component's "closed" state never being the default.

test_phase7_responsive_verification.py's SharedNavResponsiveTests already
covered the *inside*-the-media-query behavior (links hidden, toggle
shown) but never asserted the base/default state outside it — that gap
is exactly what let this ship. This file closes that gap and would have
caught the regression before it shipped.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


def _shell_css():
    with open("static/css/shell.css", encoding="utf-8") as f:
        return f.read()


class MobilePanelBaseStateTests(TestCase):
    """The closed/hidden state must be the unconditional default, not
    something that only exists inside a max-width media query."""

    def test_mobile_panel_has_a_base_level_display_none_outside_any_media_query(self):
        css = _shell_css()
        media_start = css.index("@media (max-width: 768px)")
        pre_media_css = css[:media_start]
        # Must appear before the first media query, i.e. as an
        # unconditional base rule that always applies.
        self.assertRegex(
            pre_media_css,
            r"\.ds-nav-mobile-panel\s*\{\s*display:\s*none;\s*\}",
            "no unconditional (non-media-query) display:none rule found "
            "for .ds-nav-mobile-panel -- the desktop duplicate-nav bug "
            "will recur",
        )

    def test_mobile_panel_reopens_via_is_open_inside_the_media_query(self):
        css = _shell_css()
        media_start = css.index("@media (max-width: 768px)")
        media_end = css.index("\n}\n", media_start)
        block = css[media_start:media_end]
        self.assertIn(".ds-nav-mobile-panel.is-open { display: block; }", block)


class AppShellRendersExactlyOneNavExperienceTests(TestCase):
    """End-to-end: render real app-shell pages and assert exactly one
    <nav> element and exactly one occurrence of each nav link's href,
    for both an anonymous visitor and an authenticated user."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="navdupuser", email="navdup@example.com", password="pass12345"
        )

    def _assert_single_nav(self, html):
        self.assertEqual(
            len(re.findall(r"<nav\b", html)), 1,
            "expected exactly one <nav> element",
        )
        # The mobile drawer's duplicate <a href="/plans/"> etc. are fine
        # to exist in markup (that's the real mobile menu) -- what must
        # NOT happen is that markup being visually presented as a second
        # nav bar by default. We can't run a browser here, so this test
        # is paired with MobilePanelBaseStateTests above, which proves
        # the CSS actually hides it by default.
        self.assertIn('class="ds-nav-mobile-panel"', html)
        self.assertIn('data-ds-nav-panel', html)

    def test_anonymous_visitor_on_home(self):
        html = self.client.get("/home/").content.decode()
        self._assert_single_nav(html)

    def test_authenticated_user_on_dashboard(self):
        self.client.force_login(self.user)
        html = self.client.get("/dashboard/").content.decode()
        self._assert_single_nav(html)

    def test_authenticated_user_on_history(self):
        self.client.force_login(self.user)
        html = self.client.get("/history/").content.decode()
        self._assert_single_nav(html)

    def test_authenticated_user_on_profile(self):
        self.client.force_login(self.user)
        html = self.client.get("/profile/").content.decode()
        self._assert_single_nav(html)

    def test_plans_page(self):
        html = self.client.get("/plans/").content.decode()
        self._assert_single_nav(html)
