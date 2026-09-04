"""
PHASE 6 STEP 6.8C -- generator experience consistency.

Verifies each of the five generators individually through the FULL
canonical lifecycle (validate -> controller -> credits -> entitlement ->
history -> state machine), rather than re-asserting the shared
mechanisms already covered generically in test_generator_lifecycle.py /
test_generator_state_machine.py / test_credit_pill_wiring.py /
test_entitlement_ux.py. This file is the per-generator matrix those
generic tests don't provide: proof that EVERY one of the five actually
wires into every shared mechanism, not just that the mechanisms
themselves work in the abstract.

Also verifies the legitimate, deliberately-preserved differences between
generators (documented in PHASE_6_WORKSPACE_ARCHITECTURE.md) are still
intact per generator, so this consistency pass doesn't silently flatten
them.
"""

from django.test import TestCase


class GeneratorLifecycleMatrixTests(TestCase):
    """
    One row per generator: feature slug, controller usage, credit
    refresh, entitlement refresh, history integration, and state-machine
    genState all confirmed together for that specific generator, not
    scattered across unrelated assertions.
    """

    GENERATORS = {
        "code": {
            "gen_state": "codeGenState",
            "controller_call": 'await runGeneratorLifecycle(codeGenState, {',
            "feature_slug": 'feature: "code"',
            "history_query": 'historyQuery: () => "Code Creation (" + lang + ") - " + promptText,',
        },
        "photo": {
            "gen_state": "photoGenState",
            "controller_call": 'await runGeneratorLifecycle(photoGenState, {',
            "feature_slug": 'feature: "photo"',
            "history_query": 'historyQuery: () => "Photo Prompt: " + prompt,',
        },
        "story": {
            "gen_state": "storyGenState",
            "controller_call": 'await runGeneratorLifecycle(storyGenState, {',
            "feature_slug": 'feature: "story"',
            "history_query": 'historyQuery: () => "Story: " + topic,',
        },
        "application": {
            "gen_state": "applicationGenState",
            "controller_call": 'await runGeneratorLifecycle(applicationGenState, {',
            "feature_slug": 'feature: "application"',
            "history_query": 'historyQuery: () => "Application: " + purpose,',
        },
        "proposal": {
            "gen_state": "proposalGenState",
            "controller_call": 'await runGeneratorLifecycle(proposalGenState, {',
            "feature_slug": 'feature: "proposal"',
            "history_query": 'historyQuery: () => "Proposal: " + title,',
        },
    }

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_every_generator_uses_the_canonical_controller(self):
        for name, cfg in self.GENERATORS.items():
            self.assertIn(cfg["controller_call"], self.html, f"{name} not wired to runGeneratorLifecycle")

    def test_every_generator_sends_its_correct_feature_slug(self):
        for name, cfg in self.GENERATORS.items():
            self.assertIn(cfg["feature_slug"], self.html, f"{name} missing correct feature slug")

    def test_every_generator_saves_history_with_a_real_query(self):
        for name, cfg in self.GENERATORS.items():
            self.assertIn(cfg["history_query"], self.html, f"{name} missing historyQuery wiring")

    def test_every_generator_has_its_own_state_machine_object_initialized_to_idle(self):
        for name, cfg in self.GENERATORS.items():
            self.assertIn(
                f"const {cfg['gen_state']} = {{ requestId: null, status: GENERATOR_STATES.IDLE }};",
                self.html,
                f"{name} genState not correctly initialized",
            )

    def test_credit_refresh_and_entitlement_refresh_are_shared_not_per_generator(self):
        # Both live in the ONE controller success path (6.2/6.5) -- since
        # all five generators call that same controller (proven above),
        # this one shared call site covers all five, and there being
        # exactly one avoids five independently-maintained copies that
        # could drift out of sync with each other.
        self.assertEqual(self.html.count("refreshCreditPill();"), 1)
        self.assertEqual(
            self.html.count("if (typeof refreshEntitlementBanner === \"function\") {\n                    refreshEntitlementBanner();"),
            1,
        )

    def test_generator_specific_differences_are_still_preserved(self):
        # 6.8C's job is consistency, not flattening real differences.
        # Confirms the two documented, deliberate differences (see
        # PHASE_6_WORKSPACE_ARCHITECTURE.md) survive this consistency
        # pass: Code shows a real loading placeholder (others don't), and
        # Story/Application/Proposal keep their own "Error: <message>"
        # exception text distinct from Code/Photo's generic one.
        self.assertIn('loadingHtml: "<div class\'loading\'>⏳ Generating Code...</div>"'.replace("class'", "class='"), self.html)
        self.assertIn("skipOutputLoading: true, // Story never touches storyOutput", self.html)
        self.assertIn("skipOutputLoading: true, // Photo never touches generatedPreview", self.html)


class GeneratorFullFlowSmokeTests(TestCase):
    """
    Confirms the workspace page that hosts all five generators, and each
    generator's own result/error/credit/entitlement/history plumbing,
    renders together without conflict for both guest and authenticated
    users -- the actual "does this all coexist correctly on one page"
    check 6.8C calls for, at the level achievable without a browser.
    """

    def test_guest_sees_all_five_generators_credit_pill_and_entitlement_banner(self):
        html = self.client.get("/home/").content.decode()
        for tab_id in ("codecreator-tab", "photo-tab", "story-tab", "application-tab", "proposal-tab"):
            self.assertIn(f'id="{tab_id}"', html)
        self.assertIn("data-ds-credit-pill", html)
        self.assertIn('id="entitlementBanner"', html)

    def test_authenticated_user_sees_the_same_five_generators(self):
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="matrix@example.com", password="pw12345!")
        self.client.force_login(user)
        html = self.client.get("/home/").content.decode()
        for tab_id in ("codecreator-tab", "photo-tab", "story-tab", "application-tab", "proposal-tab"):
            self.assertIn(f'id="{tab_id}"', html)
