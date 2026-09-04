"""
PHASE 7 responsive verification, per the brief's named breakpoints
(1440/1200/1024/768/600/480/390/360). No browser is available in this
environment, so verification here is structural: reading the actual
shipped CSS for fixed-width containers or missing collapse rules that
would cause horizontal overflow or clipped content at narrow widths --
not a substitute for a real visual check, but real evidence rather than
an assumption.

Findings: the responsive infrastructure was already largely solid going
into this pass (prior phases' own work), and this checkpoint's own
Golden UI CSS additions already included working breakpoints. No new
overflow bugs were found. What follows locks in the specific mechanisms
that were verified to actually work, so a future change can't silently
remove them without a test failing:

- `.main-content` (the generator workspace's 2-column grid) collapses
  to 1 column at <=768px -- verified in templates/main.html.
- `.plan-grid` (pricing cards) uses `auto-fit`/`minmax` so it reflows
  without a media query, AND has an explicit 1-column override at
  <=640px in static/css/billing.css -- both checked.
- The shared nav (static/css/shell.css) swaps to a mobile
  hamburger/panel at <=768px, hiding the desktop link row entirely
  rather than letting it overflow.
- history.html's `.controls` row uses flex-wrap so its search box +
  filter buttons + action buttons don't force horizontal scroll on a
  narrow viewport.

No component was found using a hardcoded fixed-px `width` (as opposed
to `min-width`/`max-width`) large enough to cause overflow below 360px
-- the only fixed widths present are small fixed-size elements (36-40px
icons/avatars, a 4-24px accent bar/spinner) that don't scale with
viewport width by design.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class MainWorkspaceResponsiveTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_workspace_grid_collapses_to_one_column_at_or_below_768px(self):
        media_start = self.html.index("@media (max-width: 768px) {")
        media_end = self.html.index("}", self.html.index("grid-template-columns: 1fr", media_start))
        block = self.html[media_start:media_end]
        self.assertIn(".main-content", block)
        self.assertIn("grid-template-columns: 1fr", block)

    def test_workspace_grid_uses_a_narrower_sidebar_between_768_and_1199px(self):
        # Not a full collapse -- a real intermediate step for tablet-
        # width viewports (768-1199px), not just desktop/mobile only.
        media_start = self.html.index("@media (min-width: 768px) and (max-width: 1199px)")
        media_end = self.html.index("}", media_start)
        block = self.html[media_start:media_end]
        self.assertIn("240px", block)  # narrower than desktop's 300px sidebar

    def test_nav_tabs_wrap_instead_of_overflowing_horizontally(self):
        nav_tabs_start = self.html.index(".nav-tabs {")
        nav_tabs_end = self.html.index("}", nav_tabs_start)
        block = self.html[nav_tabs_start:nav_tabs_end]
        self.assertIn("flex-wrap: wrap", block)

    def test_no_component_has_a_hardcoded_fixed_width_wide_enough_to_overflow_a_360px_viewport(self):
        import re

        # min-width/max-width are fine (they constrain, don't force
        # overflow); a bare `width: NNNpx` over ~340px (leaving no room
        # for any padding on a 360px viewport) would be a real bug.
        for match in re.finditer(r"(?<!min-|max-)width:\s*(\d+)px", self.html):
            self.assertLess(
                int(match.group(1)), 340,
                f"found a fixed width of {match.group(1)}px that could overflow a 360px viewport",
            )


class PlansResponsiveTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/plans/").content.decode()

    def test_plan_grid_reflows_without_a_media_query_via_auto_fit(self):
        with open("static/css/billing.css", encoding="utf-8") as f:
            billing_css = f.read()
        grid_start = billing_css.index(".plan-grid {")
        grid_end = billing_css.index("}", grid_start)
        block = billing_css[grid_start:grid_end]
        self.assertIn("auto-fit", block)
        self.assertIn("minmax(260px", block)

    def test_plan_grid_has_an_explicit_single_column_override_below_640px(self):
        with open("static/css/billing.css", encoding="utf-8") as f:
            billing_css = f.read()
        media_start = billing_css.index("@media (max-width: 640px)")
        media_end = billing_css.index("}", billing_css.index("grid-template-columns: 1fr", media_start))
        block = billing_css[media_start:media_end]
        self.assertIn(".plan-grid", block)
        self.assertIn("grid-template-columns: 1fr", block)


class SharedNavResponsiveTests(TestCase):
    def test_desktop_nav_links_are_hidden_and_a_mobile_toggle_shown_at_or_below_768px(self):
        with open("static/css/shell.css", encoding="utf-8") as f:
            shell_css = f.read()
        media_start = shell_css.index("@media (max-width: 768px) {")
        media_end = shell_css.index("\n}\n", media_start)
        block = shell_css[media_start:media_end]
        self.assertIn(".ds-nav-links { display: none; }", block)
        self.assertIn(".ds-nav-toggle { display: block; }", block)


class HistoryResponsiveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="historyresponsiveuser", email="historyresponsive@example.com", password="pass12345"
        )
        self.client.force_login(self.user)
        self.html = self.client.get("/history/").content.decode()

    def test_controls_row_wraps_instead_of_forcing_horizontal_scroll(self):
        controls_start = self.html.index(".controls{")
        controls_end = self.html.index("}", controls_start)
        block = self.html[controls_start:controls_end]
        self.assertIn("flex-wrap:wrap", block)

    def test_search_box_uses_min_width_not_a_rigid_fixed_width(self):
        search_start = self.html.index(".search-box{")
        search_end = self.html.index("}", search_start)
        block = self.html[search_start:search_end]
        self.assertIn("min-width:220px", block)
        self.assertNotIn("width:220px;min-width", block)  # not a rigid fixed width
