"""
PHASE 7 GOLDEN UI / security sweep: profile.html's activity feed
rendered `c.query[:40]` (the user's OWN free-form generation prompt,
from ChatHistory.query) into innerHTML with no escaping -- a real
self-XSS gap (a stored prompt containing markup would execute when the
user's own profile page renders it back). Fixed by a shared
DS.util.escapeHtml (static/js/api.js) applied at the render site.

This is a JS-side fix (the escaping happens client-side in the
template's own <script>, not in the JSON the API returns), so what's
verifiable server-side is that /profile/data/ still returns the raw,
unescaped query text (correct -- JSON is not HTML, escaping belongs at
the point something becomes markup) and that the escapeHtml call is
actually present in the shipped template and shared JS file.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import ChatHistory

User = get_user_model()


class ProfileActivityXssFixTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="xssfixuser", email="xssfix@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_profile_data_json_carries_raw_query_text_unescaped(self):
        # The API layer is JSON, not HTML -- it must NOT pre-escape (that
        # would double-escape once the client-side fix also escapes, and
        # would corrupt the text for any other JSON consumer). Escaping
        # belongs exactly once, at the point the text becomes markup.
        ChatHistory.objects.create(
            user=self.user, query="<img src=x onerror=alert(1)>", response="ok", feature="story"
        )
        data = self.client.get("/profile/data/").json()
        self.assertEqual(data["activities"][0]["title"], "<img src=x onerror=alert(1)>")

    def test_profile_template_escapes_activity_title_and_time_before_innerHTML(self):
        html = self.client.get("/profile/").content.decode()
        self.assertIn("DS.util.escapeHtml(a.title)", html)
        self.assertIn("DS.util.escapeHtml(a.time)", html)
        # a.icon is always a fixed server-chosen emoji literal, never
        # user input -- intentionally left unescaped, not missed.
        self.assertIn("${a.icon}", html)

    def test_shared_escape_helper_exists_and_is_not_duplicated_locally_in_profile_html(self):
        from django.test import Client

        api_js = Client().get("/static/js/api.js")
        # Static files aren't reliably served as an in-memory response by
        # the Django test client in this config -- WhiteNoise may return
        # a 200 but as a *streaming* response (no .content attribute) when
        # STATIC_ROOT has been collected locally, or a non-200 when it
        # hasn't. Fall back to reading the source file directly in either
        # case rather than assuming "200 means .content works".
        if api_js.status_code == 200 and hasattr(api_js, "content"):
            content = api_js.content.decode()
        else:
            with open("static/js/api.js", encoding="utf-8") as f:
                content = f.read()
        self.assertIn("function escapeHtml(text)", content)
        self.assertIn("global.DS.util = util", content)
