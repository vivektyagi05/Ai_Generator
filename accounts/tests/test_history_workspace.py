"""
PHASE 6 STEP 6.6 -- History Workspace regression tests.

Covers what this batch actually added/changed:
  - feature_label / is_reopenable derivation in history_view()
  - reopen links using the REAL deep-link mechanism (?generator=<slug>),
    not a second, parallel routing system
  - legacy rows (no feature on record) are explicitly NOT reopenable,
    with an honest reason shown rather than guessing
  - delete/clear now require a real confirm() before submitting
  - the fake client-only "Favorite" button is gone
  - response content is rendered safely (Django's default autoescaping,
    not a second sanitizer) -- proven with an actual injected payload
  - cross-user isolation on delete (already true server-side before this
    batch -- ChatHistory.objects.filter(..., user=request.user) -- this
    locks it in with an explicit test rather than leaving it implicit)
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ChatHistory


class HistoryFeatureLabelAndReopenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_each_real_feature_gets_a_label_and_is_reopenable(self):
        expected_labels = {
            "story": "Story",
            "application": "Application",
            "proposal": "Proposal",
            "code": "Code",
            "photo": "Photo",
        }
        for feature, label in expected_labels.items():
            ChatHistory.objects.create(user=self.user, query="q", response="r", feature=feature)
        resp = self.client.get(reverse("history"))
        by_feature = {item.feature: item for item in resp.context["history"]}
        for feature, label in expected_labels.items():
            item = by_feature[feature]
            self.assertEqual(item.feature_label, label)
            self.assertTrue(item.is_reopenable)

    def test_chat_feature_has_a_label_but_is_not_reopenable(self):
        # "chat" is a valid /api/ai/ feature (and a valid ChatHistory.feature
        # value) but has no tab in main.html to reopen into -- same rule
        # AI_GENERATORS/views.py's deep-link whitelist already applies.
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="chat")
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        self.assertEqual(item.feature_label, "Chat")
        self.assertFalse(item.is_reopenable)

    def test_legacy_row_has_no_label_and_is_not_reopenable(self):
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="")
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        self.assertIsNone(item.feature_label)
        self.assertFalse(item.is_reopenable)

    def test_reopen_link_uses_the_real_deep_link_mechanism(self):
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="proposal")
        html = self.client.get(reverse("history")).content.decode()
        self.assertIn('href="/home/?generator=proposal"', html)

    def test_legacy_row_shows_honest_reason_not_a_guess(self):
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="")
        html = self.client.get(reverse("history")).content.decode()
        # Static template text, not a template variable, so it renders
        # literally (no autoescape entities) -- unlike the user-supplied
        # content covered in HistoryXssSafetyTests below.
        self.assertIn("Can't reopen (legacy entry)", html)


class HistoryDestructiveActionConfirmationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_delete_form_requires_confirmation(self):
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="code")
        html = self.client.get(reverse("history")).content.decode()
        self.assertIn("onsubmit=\"return confirm(", html)
        self.assertIn(reverse("delete_history", args=[ChatHistory.objects.get().id]), html)

    def test_clear_all_form_requires_confirmation(self):
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="code")
        html = self.client.get(reverse("history")).content.decode()
        self.assertIn(f'action="{reverse("clear_history")}"', html)
        # Both destructive forms (delete + clear) must each carry their
        # own confirm() -- not just one accidentally covering both. With
        # exactly one history item present, that's exactly 2 confirm()
        # call-sites: one for Clear All, one for that item's Delete.
        self.assertEqual(html.count("onsubmit=\"return confirm("), 2)

    def test_fake_favorite_button_is_gone(self):
        ChatHistory.objects.create(user=self.user, query="q", response="r", feature="code")
        html = self.client.get(reverse("history")).content.decode()
        self.assertNotIn("function toggleFav", html)
        self.assertNotIn('onclick="toggleFav(this)"', html)
        self.assertNotIn("⭐ Favorite", html)


class HistoryXssSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_response_content_is_escaped_not_executed(self):
        payload = "<script>alert('xss')</script>"
        ChatHistory.objects.create(user=self.user, query="hi", response=payload, feature="story")
        html = self.client.get(reverse("history")).content.decode()
        # The raw, executable tag must never appear in the response body.
        self.assertNotIn(payload, html)
        # Django's default autoescaping must have neutralized it.
        self.assertIn("&lt;script&gt;", html)

    def test_query_content_is_also_escaped(self):
        payload = "<img src=x onerror=alert(1)>"
        ChatHistory.objects.create(user=self.user, query=payload, response="ok", feature="story")
        html = self.client.get(reverse("history")).content.decode()
        self.assertNotIn(payload, html)
        self.assertIn("&lt;img", html)


class HistoryCrossUserIsolationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner@example.com", password="pw12345!")
        self.attacker = User.objects.create_user(username="attacker@example.com", password="pw12345!")
        self.item = ChatHistory.objects.create(user=self.owner, query="q", response="r", feature="code")

    def test_user_cannot_delete_another_users_history_item(self):
        self.client.force_login(self.attacker)
        self.client.post(reverse("delete_history", args=[self.item.id]))
        self.assertTrue(ChatHistory.objects.filter(id=self.item.id).exists())

    def test_user_cannot_see_another_users_history_item(self):
        self.client.force_login(self.attacker)
        resp = self.client.get(reverse("history"))
        self.assertEqual(len(resp.context["history"]), 0)

    def test_clear_all_only_clears_the_authenticated_users_own_rows(self):
        ChatHistory.objects.create(user=self.attacker, query="q2", response="r2", feature="code")
        self.client.force_login(self.attacker)
        self.client.post(reverse("clear_history"))
        self.assertTrue(ChatHistory.objects.filter(id=self.item.id).exists())  # owner's row survives
        self.assertEqual(ChatHistory.objects.filter(user=self.attacker).count(), 0)
