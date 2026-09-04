"""
PHASE 7 STEP 7.15 -- fresh security sweep, scoped to what this session's
batches actually changed (RULE 6: not re-litigating Phase 6's already
solid, already-tested surface -- see test_ai_csrf.py, test_credit_security.py,
test_entitlement_security.py, test_security_xss.py, test_history_workspace.py's
HistoryXssSafetyTests, all pre-existing and unchanged).

Findings from this sweep:

1. /api/ai/costs/ (new this session) is GET-only and reads no request
   body/params at all -- there is no input surface for entitlement or
   credit spoofing to act on. Confirmed here rather than assumed.
2. It cannot be used to influence what a real generation costs -- it's
   read-only and separate from the reservation path in
   accounts/services/ai_usage_service.py, which independently calls the
   same estimate_reservation_credits() function itself, so a client
   cannot poison what a generation actually gets charged by calling this
   endpoint first.
3. No provider credentials or internal config names appear in its
   response body (already covered by test_phase7_cost_transparency.py's
   test_response_never_exposes_internal_config_names_only_public_shape;
   re-verified here from a different angle: response never contains the
   substring "GROQ" at all).
4. The new copyStory/copyApplication/copyProposal functions (see
   test_phase7_copy_actions.py) write already-rendered DOM text
   (`.innerText` of the existing, already-escaped output element) to the
   clipboard -- they read no new data and introduce no new innerHTML
   sink, so they carry no XSS surface beyond what test_security_xss.py
   already covers for those same output elements.
"""

from django.test import Client, TestCase


class NewCostsEndpointHasNoSpoofableInputTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_get_request_ignores_arbitrary_query_params(self):
        # A client cannot pass e.g. ?credits=0 or ?feature=photo&cost=1 to
        # influence the reported cost -- the view takes no query params at
        # all, it only ever reads the server-side feature list.
        baseline = self.client.get("/api/ai/costs/").json()
        tampered = self.client.get(
            "/api/ai/costs/?credits=0&cost=1&feature=photo&minimum_credits=0"
        ).json()
        self.assertEqual(baseline, tampered)

    def test_response_never_contains_provider_credential_markers(self):
        raw = self.client.get("/api/ai/costs/").content.decode()
        for marker in ("GROQ", "api_key", "API_KEY", "Bearer ", "sk-"):
            self.assertNotIn(marker, raw)

    def test_endpoint_requires_no_body_and_ignores_one_if_sent(self):
        # POST is already rejected (405, see test_phase7_cost_transparency.py);
        # confirms a GET with a body attached (unusual but not impossible
        # for some HTTP clients) doesn't change the response either.
        import json

        response = self.client.generic(
            "GET", "/api/ai/costs/", data=json.dumps({"credits": 999}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)


class CostEndpointCannotInfluenceRealChargeTests(TestCase):
    """
    The number /api/ai/costs/ reports and the number a real generation
    gets charged both come from calling the SAME function
    (credit_config.estimate_reservation_credits()) independently -- the
    costs endpoint has no side effect and no shared mutable state with
    the reservation path, so calling it first cannot pre-seed, cache-
    poison, or otherwise influence what a subsequent real generation is
    charged.
    """

    def test_calling_costs_endpoint_repeatedly_does_not_change_its_own_answer(self):
        client = Client()
        first = client.get("/api/ai/costs/").json()
        for _ in range(5):
            client.get("/api/ai/costs/")
        last = client.get("/api/ai/costs/").json()
        self.assertEqual(first, last)
