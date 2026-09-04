"""
PHASE 8 STEP 6 -- Result Management: generic-failure Retry button.

Retry already worked mechanically before this change --
runGeneratorLifecycle() keeps genState.requestId on any failure so
clicking Generate again reuses it (server-side idempotency, no double
charge). What was missing was a real affordance: the generic failure
branch (a plain non-exhaustion, non-rate-limit, non-auth error) was
static red text with no button at all.

Cannot execute browser JS from a Django test (same limitation this
codebase's other frontend-contract tests document -- see
test_generator_lifecycle.py, test_generator_experience_consistency.py),
so this follows the same established convention: assert on the exact
rendered source proving

  - renderAiFailure() accepts and threads a retryFn through to a real
    button with a real click handler (not a decorative element with no
    behavior)
  - every one of the five generators passes ITS OWN entry function as
    retryFn -- so Retry is identical to clicking Generate again for
    that specific generator, not a shared/generic no-op
"""

from django.test import TestCase


class GenericFailureRetryButtonTests(TestCase):
    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_render_ai_failure_accepts_and_uses_retry_fn(self):
        self.assertIn("function renderAiFailure(outputEl, result, retryFn)", self.html)
        self.assertIn('retryBtn.addEventListener("click", retryFn);', self.html)

    def test_single_call_site_passes_config_retry_fn_through(self):
        self.assertEqual(
            self.html.count("renderAiFailure(config.outputEl, result, config.retryFn);"), 1
        )

    def test_every_generator_wires_its_own_retry_fn(self):
        expected = {
            "codeGenState": "retryFn: () => generateCode(),",
            "photoGenState": "retryFn: () => generateImage(),",
            "storyGenState": "retryFn: () => generateStory(),",
            "applicationGenState": "retryFn: () => generateApplication(),",
            "proposalGenState": "retryFn: () => generateProposal(),",
        }
        for gen_state, retry_line in expected.items():
            # The retryFn line must appear inside THIS generator's own
            # runGeneratorLifecycle(...) call, not just somewhere on the
            # page -- confirmed by checking it's present between this
            # call site and the next.
            start = self.html.index(f"runGeneratorLifecycle({gen_state}, {{")
            end = self.html.find("runGeneratorLifecycle(", start + 1)
            end = end if end != -1 else start + 2000
            block = self.html[start:end]
            self.assertIn(retry_line, block, f"{gen_state} is missing its own retryFn wiring")
