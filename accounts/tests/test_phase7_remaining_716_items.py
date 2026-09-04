"""
PHASE 7 -- the 3 outstanding 7.16 test-matrix items the prior checkpoint
named but had not yet double-checked against actual behavior:
network-failure rendering path, generator-switching mid-request,
credit-refresh-after-generation timing.

Each is tested against the REAL extracted source (Node, same technique
test_generator_state_machine.py already established) or the real
rendered template, not a reimplementation that could silently drift
from the actual code.

Requires `node` on PATH, same as test_generator_state_machine.py --
fails loudly rather than skipping silently if it's missing.
"""

import re
import shutil
import subprocess

from django.test import TestCase


def _run_node(js_source):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required for these tests but was not found on PATH")
    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=js_source,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(f"Node script failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


class NetworkFailureRenderingPathTests(TestCase):
    """
    7.16 item 1: does a real network failure actually render a correct,
    escaped, user-facing message -- not just classify to the right
    state-machine label (already covered by
    test_generator_state_machine.py's classify_failure_state tests)?

    aiRequest()'s own status:0 sentinel never matches the 401/
    isExhaustionError/429 branches in renderAiFailure, so it always
    falls through to the final generic-error line -- extracting just
    escapeHtml() through the end of renderAiFailure() is sufficient; the
    other branches' callees (renderSignInRequired/renderExhaustionCard/
    renderRateLimited) are never reached for this input and don't need
    to be stubbed.
    """

    def setUp(self):
        html = self.client.get("/home/").content.decode()
        start_marker = "function escapeHtml(text) {"
        end_marker = "function renderRateLimited(outputEl"
        start = html.index(start_marker)
        end = html.index(end_marker, start)
        # escapeHtml() now delegates to the real DS.util.escapeHtml
        # (static/js/api.js), which nav_app.html loads on every real
        # page before main.html's own script runs -- load the ACTUAL
        # file here too and prepend it, so this test exercises the real
        # escaping logic, not a stand-in that could silently drift from
        # it. api.js is written as `(function (global) {...})(window)`;
        # Node has no browser `window`, so alias Node's own `global`
        # object to it first -- api.js's `global.DS = ...` then lands on
        # the real Node global, which is what main.html's extracted code
        # (a bare `DS.util...` reference) needs to resolve.
        with open("static/js/api.js", encoding="utf-8") as f:
            api_js_source = f.read()
        self.source = "global.window = global;\n" + api_js_source + "\n" + html[start:end]

    def test_real_network_error_renders_the_real_escaped_message(self):
        # This is the exact shape aiRequest() actually returns on a
        # thrown fetch (see templates/main.html's aiRequest catch
        # block) -- status 0, data null, a real user-facing message.
        script = self.source + """
            const outputEl = { innerHTML: "" };
            renderAiFailure(outputEl, {
                ok: false,
                status: 0,
                data: null,
                error: "Network error. Please check your connection and try again.",
            });
            console.log(outputEl.innerHTML);
        """
        out = _run_node(script)
        self.assertIn("Network error. Please check your connection and try again.", out)
        self.assertIn('color:var(--danger-color)', out)

    def test_network_error_message_is_html_escaped_not_just_inserted_raw(self):
        # A defense-in-depth check: even though this particular message
        # is server-authored and not attacker-controlled, renderAiFailure
        # must escape whatever text it's given -- confirms escapeHtml is
        # actually wired into this exact call site, not bypassed.
        script = self.source + """
            const outputEl = { innerHTML: "" };
            renderAiFailure(outputEl, {
                ok: false,
                status: 0,
                data: null,
                error: "<script>alert(1)<\\/script>",
            });
            console.log(outputEl.innerHTML);
        """
        out = _run_node(script)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_timeout_variant_of_status_0_also_renders_correctly(self):
        # aiRequest() returns the SAME status:0 shape for a client-side
        # AbortController timeout, with a different message -- confirm
        # that variant renders too, not just the generic network-error
        # wording.
        script = self.source + """
            const outputEl = { innerHTML: "" };
            renderAiFailure(outputEl, {
                ok: false,
                status: 0,
                data: null,
                error: "The request timed out. You can retry \\u2014 it will not be charged twice.",
            });
            console.log(outputEl.innerHTML);
        """
        out = _run_node(script)
        self.assertIn("The request timed out.", out)
        self.assertIn("will not be charged twice", out)


class GeneratorSwitchingMidRequestTests(TestCase):
    """
    7.16 item 2: switching generator tabs while a request is in flight.

    Reads the actual tab-switch handler and the actual per-generator
    request-id / in-flight tracking in the real template source (not a
    black-box guess) to confirm what really happens, then asserts on
    that real, current behavior so a future regression is caught.
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_each_generator_uses_its_own_independent_request_id(self):
        # newAiRequestId() / requestId plumbing exists specifically so
        # concurrent/overlapping requests across tabs cannot cross-talk.
        # The real mechanism (verified by reading the source): it's
        # assigned exactly ONCE, inside the shared runGeneratorLifecycle
        # function, onto whichever genState object was passed in --
        # codeGenState/photoGenState/storyGenState/applicationGenState/
        # proposalGenState are 5 separate objects, so each generator
        # still gets its own independent id even though only one line of
        # code assigns it.
        self.assertIn("function newAiRequestId", self.html)
        self.assertIn("genState.requestId = newAiRequestId();", self.html)
        for state_var in (
            "codeGenState", "photoGenState", "storyGenState",
            "applicationGenState", "proposalGenState",
        ):
            self.assertIn(f"const {state_var} =", self.html)

    def test_switching_tabs_does_not_cancel_or_touch_other_tabs_dom(self):
        # The nav-tab click handler only toggles .active on nav-tab/
        # tab-content elements -- it does not call any abort/cancel
        # function, meaning an in-flight request keeps running and will
        # still render into ITS OWN tab's output element when it
        # resolves (the user sees it if they switch back), rather than
        # being silently dropped. Confirm the handler is exactly that
        # simple (no hidden cancellation side effect that would need
        # separate testing). End boundary is the comment right after
        # switchTab's real closing brace (verified by reading the full
        # function body, not guessed from indentation).
        tab_switch_start = self.html.index("function switchTab(")
        tab_switch_end = self.html.index("// Deep link on initial load", tab_switch_start)
        handler_src = self.html[tab_switch_start:tab_switch_end]
        self.assertNotIn("abort(", handler_src)
        self.assertNotIn("AbortController", handler_src)
        self.assertIn("classList", handler_src)
        # The function's own comment documents this is deliberate, not
        # an oversight -- confirm that documentation is actually there,
        # matching the behavior asserted above.
        self.assertIn("does not cancel an in-flight request", handler_src)

    def test_each_generator_output_element_is_independent(self):
        # Confirms the 5 generators render into 5 DIFFERENT output
        # elements, not one shared node -- the actual mechanism that
        # makes "switch tabs mid-request" safe: a late-resolving
        # request from Tab A can never overwrite what Tab B is showing,
        # because they write to different ids. Photo's is named
        # "generatedPreview" rather than containing "output", so this
        # checks the real, specific 5 ids rather than a generic pattern
        # that would silently undercount.
        for output_id in ("codeOutput", "generatedPreview", "storyOutput", "applicationOutput", "proposalOutput"):
            self.assertIn(f'id="{output_id}"', self.html)


class CreditRefreshAfterGenerationTimingTests(TestCase):
    """
    7.16 item 3: does the credit balance actually refresh after a
    successful generation, and roughly when?

    The real mechanism (verified by reading the source, not assumed):
    all 5 generators route through one shared runGeneratorLifecycle()
    function (not 5 separate copies), which calls refreshCreditPill()
    exactly once, synchronously, right after transitioning to the
    SUCCESS state -- refreshCreditPill() itself calls
    DS.credits.mount() (static/js/credits.js), which re-fetches the
    real server-authoritative GET /credits/balance/ endpoint.
    """

    def setUp(self):
        self.html = self.client.get("/home/").content.decode()

    def test_all_five_generators_route_through_the_one_shared_lifecycle_function(self):
        # If this ever regresses to 5 separate copies, the credit-
        # refresh guarantee below would need to be re-verified per
        # generator instead of once -- confirm the shared-function
        # premise this test class relies on is still true.
        call_sites = len(re.findall(r"await runGeneratorLifecycle\(", self.html))
        self.assertEqual(call_sites, 5)

    def test_shared_lifecycle_refreshes_credits_exactly_once_on_success(self):
        lifecycle_start = self.html.index("async function runGeneratorLifecycle(")
        lifecycle_end = self.html.index("\n        }\n\n", lifecycle_start)
        lifecycle_src = self.html[lifecycle_start:lifecycle_end]
        self.assertEqual(lifecycle_src.count("refreshCreditPill()"), 1)
        # It must be called on the SUCCESS path, not the failure path --
        # confirm it comes after the state transition to SUCCESS and
        # before the failure-path's early `return`.
        success_idx = lifecycle_src.index("GENERATOR_STATES.SUCCESS")
        refresh_idx = lifecycle_src.index("refreshCreditPill()")
        self.assertGreater(refresh_idx, success_idx)

    def test_credit_refresh_happens_synchronously_after_success_not_on_a_delay(self):
        # Confirms the refresh call sits directly in the success branch,
        # not wrapped in a setTimeout/delay that could show a stale
        # balance for a noticeable window after a generation completes.
        match = re.search(r"refreshCreditPill\(\)", self.html)
        self.assertIsNotNone(match)
        window = self.html[max(0, match.start() - 400):match.start()]
        self.assertNotIn("setTimeout", window)

    def test_refresh_credit_pill_calls_the_real_shared_credits_widget(self):
        self.assertIn(
            'function refreshCreditPill() {\n            if (window.DS && window.DS.credits) { window.DS.credits.mount(); }\n        }',
            self.html,
        )

    def test_credits_widget_re_fetches_the_real_server_authoritative_balance(self):
        with open("static/js/credits.js", encoding="utf-8") as f:
            credits_js = f.read()
        self.assertIn("/credits/balance/", credits_js)
        # It only ever displays what the server returns -- never
        # computes or decrements a balance client-side.
        self.assertIn("snapshot.available_balance", credits_js)
