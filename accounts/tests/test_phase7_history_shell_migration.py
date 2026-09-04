"""
PHASE 7 GOLDEN UI: history.html migrated onto the shared app shell
(base_app.html -> nav_app.html) instead of a standalone document with
its own purple-gradient theme (#667eea/#764ba2, unrelated to
tokens.css) and its own header (page title + a Home link only -- no
Generators/Pricing links, no credit pill).
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import ChatHistory

User = get_user_model()


class HistorySharedShellTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="historyshelluser", email="historyshell@example.com", password="pass12345"
        )
        self.client.force_login(self.user)
        ChatHistory.objects.create(
            user=self.user, query="a test story", response="once upon a time", feature="story"
        )
        self.html = self.client.get("/history/").content.decode()

    def test_uses_the_shared_nav_not_its_own_header(self):
        self.assertIn('<nav class="ds-nav">', self.html)
        # The old purple-gradient theme's signature colors are gone.
        self.assertNotIn("#667eea", self.html)
        self.assertNotIn("#764ba2", self.html)

    def test_shared_nav_offers_generators_pricing_unlike_the_old_header(self):
        self.assertIn(">Pricing<", self.html)
        self.assertIn("data-ds-credit-pill", self.html)

    def test_history_nav_link_marked_current(self):
        self.assertIn('aria-current="page">History<', self.html)

    def test_history_item_still_renders_with_its_real_data(self):
        # The migration must not have broken the actual history-list
        # rendering itself.
        self.assertIn("a test story", self.html)
        self.assertIn('class="history-item"', self.html)
        self.assertIn('id="searchInput"', self.html)

    def test_page_specific_js_hooks_are_preserved(self):
        # Every id/class the inline <script> depends on must survive the
        # re-theme unchanged -- these are the exact hooks it queries.
        for hook in ("statText", "statImage", "statCode", "historyList"):
            self.assertIn(f'id="{hook}"', self.html)
