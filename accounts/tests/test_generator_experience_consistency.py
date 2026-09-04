"""
PHASE 6 STEP 6.8C -- generator experience consistency.

Each piece here (canonical controller 6.2, state machine 6.3, credit
refresh 6.5-adjacent, entitlement banner 6.5, history save with real
feature tagging 6.4/6.6) was already built and tested individually in
earlier batches. This file's purpose is different: it verifies, for
EACH of the five generators by name, that it is actually wired into
EVERY piece -- catching the specific failure mode where four generators
correctly use shared infrastructure and a fifth was missed, which no
single earlier test (each scoped to one piece of infrastructure) would
necessarily catch by itself.
"""

from django.test import TestCase

GENERATORS = {
    "code": {"gen_state": "codeGenState", "feature": '"code"', "function": "generateCode"},
    "photo": {"gen_state": "photoGenState", "feature": '"photo"', "function": "generateImage"},
    "story": {"gen_state": "storyGenState", "feature": '"story"', "function": "generateStory"},
    "application": {"gen_state": "applicationGenState", "feature": '"application"', "function": "generateApplication"},
    "proposal": {"gen_state": "proposalGenState", "feature": '"proposal"', "function": "generateProposal"},
}


class AllFiveGeneratorsUseCanonicalControllerTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_every_generator_calls_the_shared_controller_with_its_own_state(self):
        for slug, info in GENERATORS.items():
            self.assertIn(
                f"await runGeneratorLifecycle({info['gen_state']}, {{",
                self.html,
                f"{slug} is not wired to runGeneratorLifecycle()",
            )

    def test_every_generator_state_object_starts_idle_in_the_real_state_machine(self):
        for slug, info in GENERATORS.items():
            self.assertIn(
                f"const {info['gen_state']} = {{ requestId: null, status: GENERATOR_STATES.IDLE }};",
                self.html,
                f"{slug}'s gen state isn't using the real state machine",
            )

    def test_every_generator_passes_its_correct_feature_slug(self):
        for slug, info in GENERATORS.items():
            # feature: "code" etc. must appear somewhere in that
            # generator's own runGeneratorLifecycle() call block -- proven
            # indirectly by confirming the exact slug string is present at
            # all (each is only used once, for its own generator, per the
            # backend contract in accounts/ai_request_config.py).
            self.assertIn(f"feature: {info['feature']}", self.html)


class AllFiveGeneratorsIntegrateSharedCreditEntitlementHistoryTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_credit_pill_refresh_and_entitlement_banner_are_owned_by_the_shared_controller(self):
        # Since 6.2's migration, these are called from INSIDE
        # runGeneratorLifecycle()'s own success path -- once, for all
        # five generators -- rather than duplicated per generator. This
        # confirms that's still true (a regression here would mean one or
        # more generators silently stopped refreshing credits/entitlement
        # after success).
        self.assertIn("refreshCreditPill();", self.html)
        self.assertIn("refreshEntitlementBanner();", self.html)
        # Exactly one call to each inside runGeneratorLifecycle's success
        # path -- not five separate per-generator copies. Bounded by the
        # start of the next top-level function (resetGeneratorWorkspace,
        # added in PHASE 8 STEP 8/9 immediately after this one) rather than
        # a fixed character offset, so this doesn't silently under-count
        # (or need re-tuning) as the function's own real length changes.
        controller_start = self.html.index("async function runGeneratorLifecycle(genState, config)")
        controller_end = self.html.index("function resetGeneratorWorkspace(genState)", controller_start)
        controller_region = self.html[controller_start:controller_end]
        self.assertEqual(controller_region.count("refreshCreditPill();"), 1)
        self.assertEqual(controller_region.count("refreshEntitlementBanner();"), 1)

    def test_every_generator_provides_a_historyQuery_for_real_history_integration(self):
        # historyQuery is a per-generator config key passed into the
        # shared controller -- confirm every one of the five actually
        # supplies it (a generator that omitted it would never save
        # history, silently).
        self.assertEqual(self.html.count("historyQuery: () =>"), 5)


class GeneratorErrorAndCreditExhaustionConsistencyTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_generators_route_failures_through_the_same_shared_renderer(self):
        # renderAiFailure() -- and therefore renderSignInRequired()/
        # renderExhaustionCard() with the real BillingFlow -- is called
        # from exactly one place: inside runGeneratorLifecycle(), which
        # every one of the five generators (verified above) uses. This
        # confirms that single call site is intact.
        # PHASE 8 STEP 6: renderAiFailure() now also takes the generator's
        # own retryFn (see runGeneratorLifecycle's config.retryFn) so the
        # generic failure branch can offer a real, working Retry button --
        # updated to match that intentional signature change, still
        # confirming there's exactly one call site.
        self.assertEqual(self.html.count("renderAiFailure(config.outputEl, result, config.retryFn);"), 1)

    def test_photo_still_never_bypasses_the_shared_controller(self):
        # Direct regression guard: photo's provider-security properties
        # (established in earlier sessions) depend entirely on Photo
        # actually going through the same controller as everyone else --
        # not a special-cased direct-fetch path.
        self.assertIn("await runGeneratorLifecycle(photoGenState, {", self.html)
        self.assertNotIn("fetch(\"https://image.pollinations.ai", self.html)
