"""
PHASE 6 STEP 6.8A -- multi-line Django comment tag bug, found and fixed
across 8 templates while adopting the shared app shell in main.html.

Django's `{# ... #}` comment tag is single-line only: a newline inside it
breaks recognition entirely, and the whole block renders as literal text
in the response instead of being stripped (confirmed empirically -- see
PHASE_6_WORKSPACE_UI_AUDIT.md). This was a real, pre-existing, latent bug
in base_app.html/nav_app.html (invisible until main.html became the first
page ever to render them) AND, discovered while checking for the same
pattern elsewhere, in six already-LIVE templates -- login.html,
signup.html, forget.html, verify_otp.html, base_public.html, and
nav_public.html -- meaning internal engineering notes (backend contract
references, rate-limit numbers, security reasoning) were leaking into the
rendered HTML source of real, unauthenticated, public-facing pages before
this fix.

All nine affected templates were converted to Django's real multi-line
comment tag ({% comment %}...{% endcomment %}). This file proves each
one actually renders clean now -- not just that the source was edited.
"""

from django.contrib.auth.models import User
from django.test import TestCase


LEAK_MARKERS = ("{#", "#}", "PHASE_3B_AUTH_BACKEND_CONTRACT", "enumeration resistance")


class NoLeakedTemplateCommentsTests(TestCase):
    def _assert_clean(self, html, page_name):
        for marker in LEAK_MARKERS:
            self.assertNotIn(
                marker, html, f"{page_name} leaked template comment content ({marker!r} found)"
            )

    def test_login_page_is_clean(self):
        html = self.client.get("/login/").content.decode()
        self._assert_clean(html, "login.html")

    def test_signup_page_is_clean(self):
        html = self.client.get("/signup/").content.decode()
        self._assert_clean(html, "signup.html")

    def test_forgot_password_page_is_clean(self):
        html = self.client.get("/forgot/").content.decode()
        self._assert_clean(html, "forget.html")

    def test_verify_otp_page_is_clean(self):
        # verify_otp's GET path only reads request.session["signup_email"]
        # to decide whether to render or redirect -- it doesn't query
        # PendingSignup until POST -- so only the session key is needed
        # to reach the real render path this test is checking.
        session = self.client.session
        session["signup_email"] = "probe@example.com"
        session.save()
        html = self.client.get("/verify-otp/").content.decode()
        self._assert_clean(html, "verify_otp.html")

    def test_home_generator_page_is_clean_guest(self):
        html = self.client.get("/home/").content.decode()
        self._assert_clean(html, "main.html (guest)")

    def test_home_generator_page_is_clean_authenticated(self):
        user = User.objects.create_user(username="probe2@example.com", password="pw12345!")
        self.client.force_login(user)
        html = self.client.get("/home/").content.decode()
        self._assert_clean(html, "main.html (authenticated)")

    def test_main_html_actually_uses_the_shared_app_shell(self):
        # Proves 6.8A's actual structural claim, not just "no leaked
        # comments" -- main.html must genuinely extend base_app.html and
        # render nav_app.html's real nav, not just happen to be clean.
        html = self.client.get("/home/").content.decode()
        self.assertIn('class="ds-nav"', html)
        self.assertIn('data-ds-credit-pill', html)

    def test_main_html_no_longer_duplicates_shared_scripts(self):
        html = self.client.get("/home/").content.decode()
        # api.js/credits.js are now loaded exactly once each, by
        # nav_app.html -- not a second time by main.html's own content.
        self.assertEqual(html.count('src="/static/js/api.js"'), 1)
        self.assertEqual(html.count('src="/static/js/credits.js"'), 1)
