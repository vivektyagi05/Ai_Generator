"""
PHASE 6 STEP 6.2 / 6.4 regression tests -- shared generator lifecycle
controller, migrated onto by all five generators.

Cannot execute browser JS from a Django test (see the same limitation
noted in test_code_generator_actions.py), so these assert on the exact
rendered source the same way this codebase's other frontend-contract
tests do: proving `runGeneratorLifecycle()` exists with the documented
contract, and that every one of the five generators has actually been
migrated onto it (not just that the function was added alongside the old
per-generator code, unused).
"""

from django.test import TestCase


class GeneratorLifecycleControllerTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_shared_controller_function_exists(self):
        self.assertIn("async function runGeneratorLifecycle(genState, config)", self.html)

    def test_controller_still_uses_the_existing_shared_request_and_error_layer(self):
        # PHASE_6_WORKSPACE_ARCHITECTURE.md's point: this is an extraction
        # of the outer wrapper, NOT a replacement for the already-shared
        # aiRequest()/renderAiFailure() core.
        self.assertIn("await aiRequest(config.buildPrompt()", self.html)
        # PHASE 8 STEP 6: renderAiFailure() now also takes config.retryFn
        # (real, working Retry button on generic failures) -- updated to
        # match that intentional signature change.
        self.assertIn("renderAiFailure(config.outputEl, result, config.retryFn)", self.html)

    def test_controller_owns_credit_refresh_and_history_save(self):
        self.assertIn("refreshCreditPill();", self.html)
        # PHASE 8 STEP 2/3: saveHistorySafe() now also takes the
        # generator's structured `inputs` (see
        # accounts/history_input_schema.py) alongside the pre-existing
        # query/response/feature args -- updated to match that real,
        # intentional signature change, not a relaxed assertion.
        self.assertIn("saveHistorySafe(query, data.result, config.feature, inputs);", self.html)

    def test_all_five_generators_are_migrated_onto_the_controller(self):
        for gen_state in (
            "codeGenState",
            "storyGenState",
            "applicationGenState",
            "proposalGenState",
            "photoGenState",
        ):
            self.assertIn(f"await runGeneratorLifecycle({gen_state}, {{", self.html)

    def test_old_hand_written_wrappers_are_gone_not_duplicated(self):
        # Each generator's own dedup-guard/requestId/button-disable
        # boilerplate must be gone now that the controller owns it -- not
        # left in place alongside the new controller call (which would
        # mean the migration added a second lifecycle rather than
        # replacing the first one).
        self.assertNotIn("if (codeGenState.isGenerating) return; // prevent duplicate clicks", self.html)
        self.assertNotIn("if (storyGenState.isGenerating) return; // prevent duplicate clicks", self.html)
        self.assertNotIn("if (applicationGenState.isGenerating) return; // prevent duplicate clicks", self.html)
        self.assertNotIn("if (proposalGenState.isGenerating) return; // prevent duplicate clicks", self.html)
        self.assertNotIn("if (photoGenState.isGenerating) return; // prevent duplicate clicks", self.html)

    def test_code_generator_result_rendering_behavior_is_unchanged(self):
        # The generator-specific parts (code-block extraction, copy/
        # download button wiring) moved into an onSuccess callback but
        # must still be present verbatim -- this is a refactor, not a
        # behavior change.
        self.assertIn("id=\"copyCodeBtn\"", self.html)
        self.assertIn("id=\"downloadCodeBtn\"", self.html)
        self.assertIn("codeBlockRegex", self.html)

    def test_photo_still_never_calls_the_provider_directly(self):
        # Migration must not regress the server-side provider architecture:
        # the browser only ever calls /api/ai/ with feature "photo"; it
        # must not construct or fetch a provider URL itself.
        self.assertIn('feature: "photo"', self.html)
        self.assertNotIn("image.pollinations.ai", self.html)
        self.assertNotIn("GROQ_API_KEY", self.html)

    def test_photo_and_text_generators_preserve_their_original_loading_and_error_text(self):
        # Real, deliberately-preserved differences across generators (see
        # PHASE_6_WORKSPACE_ARCHITECTURE.md) must survive the migration:
        # Story/Application/Proposal never touch their output element
        # while loading (skipOutputLoading) and render `Error: <message>`
        # on an unexpected exception; Code/Photo keep their own original
        # text too.
        self.assertIn("skipOutputLoading: true, // Story never touches storyOutput", self.html)
        self.assertIn("skipOutputLoading: true, // Application never touches applicationOutput", self.html)
        self.assertIn("skipOutputLoading: true, // Proposal never touches proposalOutput", self.html)
        self.assertIn("skipOutputLoading: true, // Photo never touches generatedPreview", self.html)
        self.assertIn("Error: ${escapeHtml(err.message)}", self.html)  # Story/Application/Proposal's own catch text
        self.assertIn(
            '<span style="color:var(--danger-color);font-weight:bold;">⚠️ Something went wrong. Please try again.</span>',
            self.html,
        )  # Photo's own original catch text, preserved via onException

