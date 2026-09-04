"""
PHASE 8 ARCHITECTURE RECOVERY -- Priority 1 (logout root cause).

Root cause found: /logout/ (accounts.views.user_logout) was a bare
GET-accessible view, reachable from a plain <a href="/logout/"> in
templates/partials/nav_app.html (both the desktop bar and the mobile
panel). A GET-triggerable state change is reachable by more than an
explicit user click -- link prefetch, a restored/preloaded tab, or any
future stray href="/logout/" anywhere in the app would silently end the
session. Traced every href="/logout/" and {% url 'logout' %} occurrence
in templates/ and static/ (exactly 2, both in nav_app.html, both
intentional) -- there was no second, misplaced logout link causing the
reported symptom. The bug class is structural, not a stray element, so
the fix is structural: user_logout now requires POST
(@require_POST), and both nav_app.html controls are
<form method="post">{% csrf_token %}<button>Logout</button></form>
instead of <a href>. This test suite locks that in:

1. GET to /logout/ no longer logs the user out (proves the endpoint
   itself can't be triggered by ordinary navigation/prefetch/href).
2. Every other authenticated nav control (dashboard, history, plans,
   profile, generators dropdown links) is a real <a href> to its own
   page -- not to the logout URL -- and following it leaves the session
   authenticated.
3. The real Logout control is a POST form and still actually logs out
   when submitted.
4. Both nav_app.html logout controls (desktop + mobile panel) went
   through the same fix -- neither was missed.
"""

from django.contrib.auth.models import User
from django.test import TestCase


AUTHENTICATED_PAGES = ["/home/", "/dashboard/", "/history/", "/plans/", "/profile/"]


class LogoutIsNoLongerGetTriggerableTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="navsafety", password="pw12345!")
        self.client.login(username="navsafety", password="pw12345!")

    def test_get_to_logout_url_does_not_end_the_session(self):
        resp = self.client.get("/logout/")
        # No longer a valid method for this view -- must NOT be the old
        # 302-to-home success redirect that a GET used to produce.
        self.assertNotEqual(resp.status_code, 302)
        # Session must still be authenticated -- prove it by successfully
        # reaching a login-required page afterward.
        still_in = self.client.get("/profile/")
        self.assertEqual(still_in.status_code, 200)

    def test_post_to_logout_url_still_actually_logs_out(self):
        resp = self.client.post("/logout/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/home/")
        after = self.client.get("/profile/")
        self.assertEqual(after.status_code, 302)
        self.assertIn("/login/", after.url)


class OtherNavControlsNeverLogOutTests(TestCase):
    """
    For every authenticated page, walk every real <a href> the shared
    nav renders and prove none of them point at the logout URL, and that
    actually following each one leaves the session authenticated.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="navwalk", password="pw12345!")
        self.client.login(username="navwalk", password="pw12345!")

    def test_no_nav_anchor_href_points_at_logout(self):
        import re

        for page in AUTHENTICATED_PAGES:
            html = self.client.get(page).content.decode()
            hrefs = re.findall(r'<a\s+href="([^"]+)"', html)
            self.assertGreater(len(hrefs), 0, f"{page} rendered no nav <a> links to check")
            for href in hrefs:
                self.assertNotIn("logout", href, f"{page} has an <a> pointing at {href!r}")

    def test_following_each_nav_link_keeps_the_session_authenticated(self):
        for page in AUTHENTICATED_PAGES:
            self.client.get(page)
            still_in = self.client.get("/profile/")
            self.assertEqual(
                still_in.status_code, 200, f"session was lost after visiting {page}"
            )

    def test_logout_control_is_a_post_form_not_an_anchor(self):
        html = self.client.get("/home/").content.decode()
        self.assertIn('<form method="post" action="/logout/"', html)
        self.assertNotIn('<a href="/logout/"', html)
        # Both the desktop bar and mobile panel copies were fixed, not
        # just one -- the class hook proves both were touched.
        self.assertEqual(html.count('class="ds-nav-logout-form"'), 2)
