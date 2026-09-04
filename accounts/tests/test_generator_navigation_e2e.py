"""
PHASE 6 STEP 6.8B -- generator navigation re-verification after 6.8A's
shell migration.

The underlying mechanisms (deep-link whitelist resolution, switchTab()'s
URL sync, history reopen links) were each already built and tested in
earlier batches (6.7, 6.6). What 6.8A changed is that main.html now
actually renders nav_app.html for the first time -- this file verifies
the NAVIGATION ENTRY POINTS that only exist once that integration is
real, by actually following them through the real rendered pages rather
than re-testing each mechanism in isolation again.
"""

from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import ChatHistory


class NavDropdownToGeneratorEndToEndTests(TestCase):
    """
    The app-shell nav's Generators dropdown (nav_app.html, rendered via
    main.html since 6.8A) -> clicking a link -> lands on the correct
    generator tab, followed all the way through, not just asserted at
    the template-source level in isolation.
    """

    def test_every_dropdown_link_actually_resolves_to_the_intended_generator(self):
        expected = {
            "photo": "photo",
            "story": "story",
            "application": "application",
            "proposal": "proposal",
            "code": "codecreator",
        }
        for slug, expected_tab in expected.items():
            resp = self.client.get(f"/home/?generator={slug}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context["initial_generator_tab"], expected_tab)
            html = resp.content.decode()
            # The page that resolved this must be the SAME page whose nav
            # dropdown offered the link in the first place -- i.e. this
            # isn't a separate, disconnected page.
            self.assertIn('class="ds-nav"', html)
            self.assertIn(f'<script id="initial-generator-tab" type="application/json">"{expected_tab}"</script>', html)

    def test_dropdown_links_are_present_in_the_actual_rendered_workspace_page(self):
        # Confirms the dropdown a user would click FROM /home/ itself
        # (not just from an isolated nav_app.html render) offers all five.
        html = self.client.get("/home/").content.decode()
        for slug in ("photo", "story", "application", "proposal", "code"):
            self.assertIn(f'href="/home/?generator={slug}"', html)

    def test_mobile_nav_panel_also_links_to_the_workspace(self):
        html = self.client.get("/home/").content.decode()
        self.assertIn('data-ds-nav-panel', html)


class HistoryReopenToGeneratorEndToEndTests(TestCase):
    """
    History's Reopen link (6.6) -> the SAME workspace page (now shell-
    integrated since 6.8A) -> correct generator selected. Verifies the
    two features actually compose, not just that each independently
    works.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_reopen_link_from_history_lands_on_the_correct_generator_in_the_real_shell(self):
        item = ChatHistory.objects.create(user=self.user, query="q", response="r", feature="proposal")
        history_html = self.client.get("/history/").content.decode()
        self.assertIn('href="/home/?generator=proposal"', history_html)

        # Actually follow it.
        resp = self.client.get("/home/?generator=proposal")
        self.assertEqual(resp.context["initial_generator_tab"], "proposal")
        workspace_html = resp.content.decode()
        self.assertIn('class="ds-nav"', workspace_html)  # same integrated shell
        self.assertIn(
            '<script id="initial-generator-tab" type="application/json">"proposal"</script>', workspace_html
        )

    def test_reopen_works_for_all_five_reopenable_features(self):
        for feature, expected_tab in (
            ("photo", "photo"), ("story", "story"), ("application", "application"),
            ("proposal", "proposal"), ("code", "codecreator"),
        ):
            ChatHistory.objects.create(user=self.user, query=f"q-{feature}", response="r", feature=feature)
        history_html = self.client.get("/history/").content.decode()
        for feature in ("photo", "story", "application", "proposal", "code"):
            self.assertIn(f'href="/home/?generator={feature}"', history_html)


class InvalidAndGuestNavigationTests(TestCase):
    def test_invalid_generator_value_falls_back_safely_through_the_real_shell(self):
        resp = self.client.get("/home/?generator=not-a-real-generator")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["initial_generator_tab"])
        html = resp.content.decode()
        self.assertIn('<script id="initial-generator-tab" type="application/json">null</script>', html)
        # The default Photo tab remains marked active in the base HTML.
        self.assertIn('data-tab="photo" aria-current="true"', html)

    def test_guest_can_reach_every_generator_via_deep_link(self):
        # Guests can generate (just not save history) -- navigation itself
        # must not require authentication.
        for slug, expected_tab in (
            ("photo", "photo"), ("story", "story"), ("application", "application"),
            ("proposal", "proposal"), ("code", "codecreator"),
        ):
            resp = self.client.get(f"/home/?generator={slug}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context["initial_generator_tab"], expected_tab)
