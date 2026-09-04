"""
PHASE 6 STEP 6.3 -- formal generator state machine, behaviorally tested.

Every other frontend test in this codebase (test_code_generator_actions.py,
test_generator_lifecycle.py, etc.) asserts on rendered source text, because
there's no browser to run real JS in. The state machine is pure logic with
no DOM dependency, though, so this test does something stronger: it
extracts the EXACT source between the
PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_START/_END markers in
templates/main.html and actually executes it under Node, then drives real
transitions and asserts on the real return values -- not a
reimplementation of the transition table that could silently drift from
the real one.

Requires `node` on PATH. If it isn't available, these tests fail loudly
(not silently skip) so a missing test dependency is never mistaken for a
passing state machine.
"""

import re
import shutil
import subprocess

from django.test import TestCase

START_MARKER = "// PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_START"
END_MARKER = "// PHASE_6_STATE_MACHINE_TESTABLE_BLOCK_END"


def _extract_state_machine_source(html):
    start = html.index(START_MARKER)
    end = html.index(END_MARKER, start)
    return html[start:end]


def _run_node(js_source):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required to run PHASE 6 state machine tests but was not found on PATH")
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


class GeneratorStateMachineBehaviorTests(TestCase):
    def setUp(self):
        html = self.client.get("/home/").content.decode()
        self.state_machine_source = _extract_state_machine_source(html)

    def _run(self, script_body):
        # extractApiError() (part of the extracted block) is the only
        # dependency the state-machine functions actually call at
        # runtime; everything else in the block is self-contained pure
        # logic -- no document/window/fetch stub needed.
        full_script = self.state_machine_source + "\n" + script_body
        return _run_node(full_script)

    def test_extraction_actually_found_the_real_block(self):
        # Guards against the markers silently drifting apart from the
        # real code (e.g. someone edits main.html and the END marker ends
        # up before new logic that should have been included).
        self.assertIn("const GENERATOR_STATES", self.state_machine_source)
        self.assertIn("function setGeneratorState", self.state_machine_source)
        self.assertIn("function classifyFailureState", self.state_machine_source)

    def test_idle_to_validating_allowed(self):
        out = self._run("""
            const gs = { status: GENERATOR_STATES.IDLE };
            const ok = setGeneratorState(gs, GENERATOR_STATES.VALIDATING);
            console.log(JSON.stringify({ ok, status: gs.status }));
        """)
        self.assertEqual(out.strip(), '{"ok":true,"status":"VALIDATING"}')

    def test_idle_to_success_directly_is_rejected(self):
        # This IS the double-submit-shaped bug class Phase 6 exists to
        # prevent: state must not become SUCCESS without ever having been
        # REQUESTING.
        out = self._run("""
            const gs = { status: GENERATOR_STATES.IDLE };
            const ok = setGeneratorState(gs, GENERATOR_STATES.SUCCESS);
            console.log(JSON.stringify({ ok, status: gs.status }));
        """)
        self.assertEqual(out.strip(), '{"ok":false,"status":"IDLE"}')
        # Critically: status is unchanged on a rejected transition.

    def test_requesting_to_requesting_is_rejected(self):
        # The literal shape of a duplicate-submission race: a second
        # click firing while already REQUESTING must not be able to
        # re-enter REQUESTING (which is exactly what the dedup guard in
        # runGeneratorLifecycle also checks BEFORE ever calling
        # setGeneratorState -- this proves the state machine itself
        # would refuse it even if that outer guard were ever removed).
        out = self._run("""
            const gs = { status: GENERATOR_STATES.REQUESTING };
            const ok = setGeneratorState(gs, GENERATOR_STATES.REQUESTING);
            console.log(JSON.stringify({ ok, status: gs.status }));
        """)
        self.assertEqual(out.strip(), '{"ok":false,"status":"REQUESTING"}')

    def test_every_requesting_outcome_is_reachable(self):
        outcomes = [
            "SUCCESS", "ERROR", "AUTH_REQUIRED", "PLAN_NOT_ALLOWED",
            "CREDITS_EXHAUSTED", "QUOTA_EXCEEDED", "RATE_LIMITED", "NETWORK_ERROR",
        ]
        out = self._run(f"""
            const outcomes = {outcomes!r};
            const results = outcomes.map(function(target) {{
                const gs = {{ status: GENERATOR_STATES.REQUESTING }};
                const ok = setGeneratorState(gs, GENERATOR_STATES[target]);
                return ok && gs.status === target;
            }});
            console.log(JSON.stringify(results));
        """.replace("'", '"'))
        self.assertEqual(out.strip(), "[true,true,true,true,true,true,true,true]")

    def test_every_terminal_state_can_retry_or_reset(self):
        terminals = [
            "SUCCESS", "ERROR", "AUTH_REQUIRED", "PLAN_NOT_ALLOWED",
            "CREDITS_EXHAUSTED", "QUOTA_EXCEEDED", "RATE_LIMITED", "NETWORK_ERROR",
        ]
        out = self._run(f"""
            const terminals = {terminals!r}.map(function(t) {{ return t; }});
            const results = terminals.map(function(t) {{
                const retryState = {{ status: GENERATOR_STATES[t] }};
                const retryOk = setGeneratorState(retryState, GENERATOR_STATES.VALIDATING);
                const resetState = {{ status: GENERATOR_STATES[t] }};
                const resetOk = setGeneratorState(resetState, GENERATOR_STATES.IDLE);
                return retryOk && resetOk;
            }});
            console.log(JSON.stringify(results));
        """.replace("'", '"'))
        self.assertEqual(out.strip(), "[true,true,true,true,true,true,true,true]")

    def test_a_completed_request_cannot_remain_stuck_in_requesting(self):
        # REQUESTING has no self-transition and no transition back to
        # IDLE/VALIDATING directly -- it MUST resolve to SUCCESS or a
        # specific failure state. This proves that shape structurally
        # (not just "some tests pass"): every one of REQUESTING's allowed
        # targets is a resolved (non-REQUESTING, non-VALIDATING) state.
        out = self._run("""
            const allowed = GENERATOR_STATE_TRANSITIONS[GENERATOR_STATES.REQUESTING];
            const allResolved = allowed.every(function(s) {
                return s !== GENERATOR_STATES.REQUESTING && s !== GENERATOR_STATES.VALIDATING;
            });
            console.log(JSON.stringify({ allResolved, count: allowed.length }));
        """)
        self.assertEqual(out.strip(), '{"allResolved":true,"count":8}')

    def test_classify_failure_state_maps_real_backend_shapes_correctly(self):
        # These are the EXACT response shapes AI_GENERATORS/api_views.py
        # produces for each denial reason (see
        # PHASE_6_WORKSPACE_ARCHITECTURE.md / api_views.py itself) -- not
        # invented shapes, so this proves the state machine and the real
        # backend contract agree.
        cases = [
            ({"status": 0, "data": None}, "NETWORK_ERROR"),
            ({"status": 401, "data": None}, "AUTH_REQUIRED"),
            ({"status": 402, "data": {"error": "Insufficient credits"}}, "CREDITS_EXHAUSTED"),
            (
                {"status": 429, "data": {"error": {"code": "QUOTA_EXCEEDED", "message": "Daily limit reached"}}},
                "QUOTA_EXCEEDED",
            ),
            ({"status": 429, "data": {"error": "Too many requests. Please slow down and try again shortly."}}, "RATE_LIMITED"),
            (
                {"status": 403, "data": {"error": {"code": "PLAN_NOT_ALLOWED", "message": "Upgrade required"}}},
                "PLAN_NOT_ALLOWED",
            ),
            ({"status": 500, "data": {"error": "Internal error"}}, "ERROR"),
        ]
        for result, expected in cases:
            import json as _json

            out = self._run(f"""
                const result = {_json.dumps(result)};
                console.log(classifyFailureState(result));
            """)
            self.assertEqual(out.strip(), expected, f"failed for {result}")
