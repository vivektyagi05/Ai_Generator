"""
PHASE 7 GOLDEN UI: profile.html migrated onto the shared app shell
(base_app.html -> nav_app.html) instead of being a standalone document
with its own header (site title + Home link + Logout button only -- no
History/Pricing/Generators links, no credit pill). Its own :root token
block (byte-for-byte duplicate of static/css/tokens.css, per that
file's own header comment) is also removed -- one real source of truth
for the palette instead of two.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class ProfileSharedShellTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profileshelluser", email="profileshell@example.com", password="pass12345"
        )
        self.client.force_login(self.user)
        self.html = self.client.get("/profile/").content.decode()

    def test_uses_the_shared_nav_not_its_own_header(self):
        self.assertIn('<nav class="ds-nav">', self.html)
        self.assertNotIn('class="header">', self.html)
        self.assertNotIn("logoutBtn", self.html)

    def test_shared_nav_offers_generators_history_pricing_unlike_the_old_header(self):
        # The old standalone header only ever had a Home link and a
        # Logout button -- History/Pricing were not reachable from this
        # page at all before this migration.
        self.assertIn(">History<", self.html)
        self.assertIn(">Pricing<", self.html)
        self.assertIn('data-ds-credit-pill', self.html)

    def test_profile_nav_link_marked_current(self):
        self.assertIn(
            'href="/profile/" class="ds-btn ds-btn-secondary" aria-current="page"', self.html
        )

    def test_logout_still_reachable_via_the_shared_nav_link(self):
        self.assertIn(">Logout<", self.html)

    def test_root_token_block_not_duplicated_on_this_page(self):
        # tokens.css (loaded once by base_app.html) is now the only
        # source -- this page no longer redeclares the same values
        # locally.
        self.assertNotIn(":root {", self.html)

    def test_profile_specific_content_still_renders(self):
        # The migration must not have broken the actual profile content
        # itself -- spot-check a few real, page-specific elements.
        self.assertIn('id="avatarDisplay"', self.html)
        self.assertIn('class="profile-header"', self.html)
        self.assertIn(self.user.email, self.html)
