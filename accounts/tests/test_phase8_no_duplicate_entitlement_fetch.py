"""
PHASE 8 STEP 10 -- Performance audit: duplicate /api/entitlements/
request on page load.

Before this fix, a deep-linked page load (e.g. /home/?generator=photo)
fired refreshEntitlementBanner() TWICE: once from switchTab() (called
inside the DOMContentLoaded handler whenever a deep link is present --
see its own PHASE 6 STEP 6.5 comment), and once more from a second,
unconditional `document.addEventListener("DOMContentLoaded",
refreshEntitlementBanner)` registered separately further down the file.
Two /api/entitlements/ requests firing for the exact same initial
banner state on every deep-linked load.

Covers:
  - the standalone unconditional listener is gone
  - the single DOMContentLoaded handler covers both cases (deep link
    present -> via switchTab(); no deep link -> direct call) so exactly
    one initial call happens either way
"""

from django.test import TestCase


class NoDuplicateEntitlementFetchOnLoadTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_no_standalone_unconditional_listener_remains(self):
        self.assertNotIn(
            'addEventListener("DOMContentLoaded", refreshEntitlementBanner)', self.html
        )

    def test_single_dom_content_loaded_handler_covers_both_paths(self):
        # Anchored on unique text right after the opening brace (a plain
        # `document.addEventListener("DOMContentLoaded", function () {`
        # also appears once, earlier, in nav_app.html's own unrelated
        # DS.credits.mount() listener -- this anchor is specific to the
        # one in main.html).
        start = self.html.index(
            'document.addEventListener("DOMContentLoaded", function () {\n'
            '        const raw = document.getElementById("initial-generator-tab");'
        )
        end = self.html.index("restoreReopenedInputs();", start)
        block = self.html[start:end]
        # Deep-link path: via switchTab(), not a second direct call.
        self.assertIn("switchTab(initialTab, null, false);", block)
        # No-deep-link path: exactly one direct call, in the else branch.
        self.assertEqual(block.count("refreshEntitlementBanner();"), 1)
