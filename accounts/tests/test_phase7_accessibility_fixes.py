"""
PHASE 7 accessibility pass (structural/code-level -- no browser
available, same constraint as the responsive-verification file). Found
2 real gaps by grepping every `<img` tag in the migrated/re-themed
templates for a missing `alt` attribute, and 1 more by checking form
inputs for an accessible name beyond a placeholder (which disappears on
focus/input and isn't reliably announced by every screen reader):

1. The photo generator's result `<img>` (templates/main.html, built via
   JS template string) had no `alt` at all.
2. history.html's per-item result `<img>` (Django template, for
   `item.type == "image"` rows) had no `alt` at all.
3. history.html's search `<input>` had a placeholder only, no `<label>`
   -- fixed with a new shared `.sr-only` utility class, moved from being
   a page-local copy inside main.html's own `<style>` block (which
   history.html had no access to) into static/css/components.css, which
   every page already loads via base_app.html/base_public.html.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import ChatHistory

User = get_user_model()


class PhotoGeneratorAltTextTests(TestCase):
    def test_generated_photo_img_tag_includes_alt_text_built_from_the_prompt(self):
        html = self.client.get("/home/").content.decode()
        img_line = 'previewEl.innerHTML = `<img src="${imageUrl}" class="preview-image" alt="${escapeHtml(prompt)}">`;'
        self.assertIn(img_line, html)

    def test_alt_text_is_escaped_not_inserted_raw(self):
        # The prompt is free-form user input landing inside an HTML
        # attribute -- must go through the same escapeHtml() the rest
        # of this template already uses for user-supplied text.
        html = self.client.get("/home/").content.decode()
        img_start = html.index('previewEl.innerHTML = `<img')
        img_end = html.index('`;', img_start)
        img_line = html[img_start:img_end]
        self.assertIn("escapeHtml(prompt)", img_line)


class HistoryImageAltTextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="a11yimguser", email="a11yimg@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_image_history_item_renders_alt_text_from_its_own_query(self):
        ChatHistory.objects.create(
            user=self.user,
            query="a golden retriever puppy in a meadow",
            response="https://example.com/generated.png",
            feature="photo",
        )
        html = self.client.get("/history/").content.decode()
        self.assertIn('alt="a golden retriever puppy in a meadow"', html)

    def test_alt_text_is_html_escaped_by_django_autoescape(self):
        # Django autoescapes {{ item.query }} by default -- confirm a
        # query containing markup renders safely, not as live HTML.
        ChatHistory.objects.create(
            user=self.user,
            query='<script>alert(1)</script>',
            response="https://example.com/generated.png",
            feature="photo",
        )
        html = self.client.get("/history/").content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class HistorySearchInputLabelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="a11ysearchuser", email="a11ysearch@example.com", password="pass12345"
        )
        self.client.force_login(self.user)
        self.html = self.client.get("/history/").content.decode()

    def test_search_input_has_an_associated_label(self):
        self.assertIn('<label for="searchInput" class="sr-only">Search history</label>', self.html)
        self.assertIn('id="searchInput"', self.html)

    def test_shared_sr_only_utility_class_is_actually_defined(self):
        with open("static/css/components.css", encoding="utf-8") as f:
            components_css = f.read()
        self.assertIn(".sr-only {", components_css)
        self.assertIn("clip: rect(0, 0, 0, 0);", components_css)

    def test_main_html_no_longer_duplicates_the_sr_only_definition(self):
        home_html = self.client.get("/home/").content.decode()
        self.assertEqual(home_html.count(".sr-only {"), 0)
