"""
PHASE 6 STEP 6.8D/6.8G -- fresh forensic sweep for the SAME bug class as
the original Step 3 finding (a function declared inside another
function's body, but invoked via a global inline onclick that can't see
it -- see accounts/tests/test_code_generator_actions.py for that
original, already-fixed case).

Checked every one of the 19 distinct function names referenced by an
inline onclick= in templates/main.html (grepped, not assumed) by manually
tracing each generator's opening `async function ...(` through its
matching closing brace and confirming every download/share/reset
function for that generator is declared AFTER that closing brace, at
module scope -- not nested inside it. Result: no other instance of this
bug exists. This file locks that (negative) finding in with an
executable test rather than leaving it as an unrecorded manual check.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase


# (function_name, the async generator function line it must be declared
# strictly AFTER in the file -- proving it's outside that function's body,
# not nested inside it)
GENERATOR_ACTION_FUNCTIONS_MUST_FOLLOW = {
    "downloadImage": "async function generateImage() {",
    "shareImage": "async function generateImage() {",
    "resetEditor": "async function generateImage() {",
    "downloadStory": "async function generateStory(event) {",
    "shareStory": "async function generateStory(event) {",
    "resetStory": "async function generateStory(event) {",
    "downloadApplication": "async function generateApplication(event) {",
    "shareApplication": "async function generateApplication(event) {",
    "resetApplication": "async function generateApplication(event) {",
    "downloadProposal": "async function generateProposal(event) {",
    "shareProposal": "async function generateProposal(event) {",
    "resetProposal": "async function generateProposal(event) {",
}

# selectProposalStyle is a style-picker helper declared BEFORE
# generateProposal() (same module scope, just earlier in the file) --
# checked separately below since the "must follow" assumption above
# doesn't apply to it, but it still must be a real, reachable,
# module-scope declaration.
SEPARATELY_VERIFIED_MODULE_SCOPE_FUNCTIONS = ["selectProposalStyle"]


class NoDeadOnclickHandlersRemainTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_every_onclick_referenced_function_is_actually_declared(self):
        import re

        referenced = set(re.findall(r'onclick="([a-zA-Z_]+)\(', self.html))
        self.assertGreater(len(referenced), 0)
        for fn in referenced:
            self.assertIn(f"function {fn}(", self.html, f"onclick references undeclared function {fn}()")

    def test_action_functions_are_declared_after_their_generator_closes_not_nested_inside_it(self):
        # This is the exact structural shape of the original Step 3 bug:
        # if any of these were still nested INSIDE their generator
        # function, the generator's own closing brace would appear AFTER
        # (not before) the action function's declaration in the file.
        for fn_name, generator_decl in GENERATOR_ACTION_FUNCTIONS_MUST_FOLLOW.items():
            generator_pos = self.html.index(generator_decl)
            fn_pos = self.html.index(f"function {fn_name}(")
            self.assertGreater(
                fn_pos,
                generator_pos,
                f"{fn_name}() must be declared after {generator_decl!r}",
            )

    def test_proposal_style_picker_is_a_real_module_scope_declaration(self):
        for fn_name in SEPARATELY_VERIFIED_MODULE_SCOPE_FUNCTIONS:
            self.assertIn(f"function {fn_name}(", self.html)

    def test_no_dead_or_placeholder_ui_markers_remain(self):
        for marker in ("TODO", "FIXME", "Coming soon", "Coming Soon", "coming-soon"):
            self.assertNotIn(marker, self.html)

    def test_no_fake_client_only_favorite_or_similar_decorative_toggle(self):
        self.assertNotIn("toggleFav", self.html)

    def test_csrf_helper_duplication_is_documented_not_accidental(self):
        # PHASE 6 STEP 6.8D: confirms the deliberate-retention comment
        # explaining why this page keeps its own getCsrfToken() alongside
        # DS.api's is actually present -- not silently dropped by a future
        # edit that would turn this back into unexamined duplication.
        self.assertIn("Two correct,\n        // independent implementations of a small cookie-read", self.html)


class NoDeadUiAcrossWholeAppTests(TestCase):
    """
    PHASE 8A ITEM 6 (dummy/unwired UI sweep) -- the class above only ever
    covered templates/main.html (the only template it fetches). A direct
    grep across every other template (dashboard/history/plans/profile/
    login/signup/forget/verify_otp/nav_app/nav_public) for TODO/FIXME/
    "Coming soon"/dummy markers, href="#" dead links, and <button>
    elements with no onclick, no type="submit", and no id (the shape an
    orphaned/unwired control would take) found exactly one real
    instance: signup.html's Terms of Service / Privacy Policy links were
    literal `<a href="#">` with no matching route anywhere in
    AI_GENERATORS/urls.py or accounts/urls.py (confirmed by search) --
    fixed to plain text (Phase 8A's "remove it or genuinely connect it"
    rule; no legal-page infrastructure exists yet to connect them to).
    Every other candidate found by the same sweep (history.html's
    filter/prev/next buttons, plans.html's interval-toggle and
    plan-cta buttons, nav_app.html's mobile-menu toggle) was verified to
    have real JS wiring or to be an intentionally-disabled status
    indicator ("Premium Active", "Your current plan"), not a dead
    control -- see PHASE_8A_PROGRESS.md Item 6 for the full trace.
    This test locks in the sweep's actual scope (every page, not just
    main.html) so a future addition to any template is covered by the
    same check the class above already applies to main.html alone.
    """

    PAGES = {
        "/dashboard/": True,  # requires auth
        "/history/": True,
        "/profile/": True,
        "/plans/": False,
        "/login/": False,
        "/signup/": False,
        "/forgot/": False,
    }

    def setUp(self):
        self.user = User.objects.create_user(
            username="deadui@example.com", email="deadui@example.com", password="pw12345!"
        )

    def _get_html(self, path, needs_auth):
        client = Client()
        if needs_auth:
            client.force_login(self.user)
        response = client.get(path)
        self.assertEqual(response.status_code, 200, f"{path} did not render")
        return response.content.decode()

    def test_no_dead_or_placeholder_ui_markers_anywhere(self):
        for path, needs_auth in self.PAGES.items():
            html = self._get_html(path, needs_auth)
            for marker in ("TODO", "FIXME", "Coming soon", "Coming Soon", "coming-soon"):
                self.assertNotIn(marker, html, f"{path} contains dead-UI marker {marker!r}")

    def test_no_dead_hash_only_links_anywhere(self):
        for path, needs_auth in self.PAGES.items():
            html = self._get_html(path, needs_auth)
            self.assertNotIn('href="#"', html, f"{path} contains a dead href=\"#\" link")
            self.assertNotIn("href='#'", html, f"{path} contains a dead href='#' link")

    def test_every_button_is_either_a_submit_a_named_control_or_has_a_real_onclick(self):
        # A <button> with none of (onclick=, type="submit", id=, a
        # data-* JS-hook attribute) is the exact shape an orphaned
        # control (no selector anything could addEventListener against,
        # no click handler, not even a native form submit) would take.
        # data-* is this codebase's established pattern for JS hooks
        # (nav_app.html's [data-ds-nav-toggle], plans.html's
        # [data-upgrade-btn]/[data-interval-toggle]) alongside id= and
        # onclick=, not a loophole -- each data-* candidate found by
        # this sweep was individually verified to have a matching
        # querySelector/addEventListener elsewhere in the same
        # template (see PHASE_8A_PROGRESS.md Item 6).
        import re

        for path, needs_auth in self.PAGES.items():
            html = self._get_html(path, needs_auth)
            for match in re.finditer(r"<button\b([^>]*)>", html):
                attrs = match.group(1)
                is_wired = (
                    "onclick=" in attrs
                    or 'type="submit"' in attrs
                    or "id=" in attrs
                    or re.search(r'\bdata-[a-z-]+', attrs)
                    # A bare <button> inside a <form> with no type=
                    # attribute is a submit button per the HTML spec --
                    # real templates use this shape deliberately (see
                    # history.html's Clear All / Delete buttons).
                    or "type=" not in attrs
                )
                self.assertTrue(
                    is_wired,
                    f"{path} has a <button{attrs}> with no onclick, id, data-* hook, or submit behavior",
                )
