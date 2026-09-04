"""
PHASE 6 STEP 7 (deep linking) / PHASE_2_FRONTEND_GAP_MATRIX.md GAP-05.

Covers:
  - each of the 5 valid ?generator= values resolves to the correct tab id,
    server-side (AI_GENERATORS/views.py:create())
  - an unrecognized/missing value resolves to no deep link (None) rather
    than an error or a guessed default -- the client-side fallback (the
    Photo tab, already marked active in the base HTML) handles it
  - "chat" is deliberately NOT a valid deep-link target (no chat tab
    exists in main.html, even though it's a valid /api/ai/ feature)
  - the value is only ever passed through Django's `json_script` filter
    (safe JSON in a typed <script> tag), never interpolated directly into
    HTML/JS -- asserted by checking the exact rendered markers
"""

from django.test import TestCase


class GeneratorDeepLinkBackendTests(TestCase):
    def test_valid_generator_params_map_to_expected_tabs(self):
        expected = {
            "photo": "photo",
            "story": "story",
            "application": "application",
            "proposal": "proposal",
            "code": "codecreator",
        }
        for param, expected_tab in expected.items():
            resp = self.client.get(f"/home/?generator={param}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.context["initial_generator_tab"], expected_tab)

    def test_missing_generator_param_resolves_to_none(self):
        resp = self.client.get("/home/")
        self.assertIsNone(resp.context["initial_generator_tab"])

    def test_unrecognized_generator_param_resolves_to_none_not_an_error(self):
        resp = self.client.get("/home/?generator=totally-not-real")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["initial_generator_tab"])

    def test_chat_is_not_a_valid_deep_link_target(self):
        # "chat" is a valid /api/ai/ feature but has no tab in main.html --
        # deep-linking to it must NOT be treated as valid.
        resp = self.client.get("/home/?generator=chat")
        self.assertIsNone(resp.context["initial_generator_tab"])

    def test_generator_param_is_never_reflected_raw_into_the_page(self):
        # A hostile query value must only ever be able to select among the
        # fixed whitelist -- it must never appear verbatim in the response
        # body (that would indicate raw interpolation and a potential XSS
        # surface).
        hostile = "<script>alert(1)</script>"
        resp = self.client.get(f"/home/?generator={hostile}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(hostile, resp.content.decode())


class GeneratorDeepLinkFrontendWiringTests(TestCase):
    def test_valid_deep_link_renders_as_safe_json_script(self):
        resp = self.client.get("/home/?generator=code")
        html = resp.content.decode()
        self.assertIn('<script id="initial-generator-tab" type="application/json">"codecreator"</script>', html)

    def test_no_deep_link_renders_null_not_missing(self):
        resp = self.client.get("/home/")
        html = resp.content.decode()
        self.assertIn('<script id="initial-generator-tab" type="application/json">null</script>', html)

    def test_nav_buttons_carry_data_tab_for_programmatic_lookup(self):
        html = self.client.get("/home/").content.decode()
        for tab_id in ("photo", "story", "application", "proposal", "codecreator"):
            self.assertIn(f'data-tab="{tab_id}"', html)

    def test_switchTab_reads_initial_deep_link_on_load(self):
        html = self.client.get("/home/").content.decode()
        self.assertIn('getElementById("initial-generator-tab")', html)
        self.assertIn("switchTab(initialTab, null, false)", html)

    def test_popstate_handler_present_for_back_forward(self):
        html = self.client.get("/home/").content.decode()
        self.assertIn('addEventListener("popstate"', html)
