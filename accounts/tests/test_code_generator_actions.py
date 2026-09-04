"""
PHASE 5 STEP 3 regression test -- Code generator Quick Actions scope bug.

Cannot execute browser JS from a Django test, so this asserts on the
rendered template the same way this codebase's existing frontend-contract
tests do (see test_forgot_password_frontend.py) -- proving:

  1. The dead inline handlers that referenced functions scoped inside
     generateCode() are gone from the rendered page.
  2. The remaining Reset control has a real id and no inline onclick
     reaching into another function's local scope.
  3. resetCodeGenerator() is declared at module level (outside
     generateCode()'s body), so a real browser's addEventListener wiring
     at that same module level can actually reach it -- regression target
     for "declared locally but invoked by a global inline handler".
"""

from django.test import TestCase


class CodeGeneratorActionScopeTests(TestCase):
    def setUp(self):
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 200)
        self.html = response.content.decode()

    def test_dead_inline_handlers_are_gone(self):
        self.assertNotIn('onclick="downloadGeneratedCode()"', self.html)
        self.assertNotIn('onclick="copyGeneratedCode()"', self.html)
        self.assertNotIn('onclick="resetCodeGenerator()"', self.html)

    def test_reset_button_has_a_real_id_for_delegated_wiring(self):
        self.assertIn('id="resetCodeBtn"', self.html)

    def test_reset_function_is_declared_outside_generateCode_body(self):
        # generateCode() opens...
        gen_start = self.html.index("async function generateCode()")
        # ...and resetCodeGenerator's declaration must appear BEFORE that
        # (module level, alongside codeGenState), not after/nested within
        # generateCode()'s own body as it was pre-fix.
        reset_decl = self.html.index("function resetCodeGenerator()")
        self.assertLess(
            reset_decl,
            gen_start,
            "resetCodeGenerator() must be declared at module scope, before generateCode(), "
            "not nested inside it.",
        )

    def test_reset_is_wired_via_addEventListener_not_inline_onclick(self):
        self.assertIn(
            'getElementById("resetCodeBtn")?.addEventListener("click", resetCodeGenerator)', self.html
        )

    def test_working_dynamic_copy_download_buttons_still_present(self):
        # The real, correctly-scoped copy/download buttons that
        # generateCode() renders alongside its result are untouched.
        self.assertIn('id="copyCodeBtn"', self.html)
        self.assertIn('id="downloadCodeBtn"', self.html)
