"""
PHASE 8 STEP 11 -- fresh security sweep, scoped to what Phase 8 actually
added/changed (per the spec: "do not merely quote Phase 6/7 audit
documents... inspect the final implementation"). Earlier Phase 8 batches
already carry their own security-relevant tests (cross-user isolation on
reopen/dashboard, sanitize_inputs whitelist rejecting unknown keys, a
non-numeric/injection-shaped reopen id). This file covers what those
didn't:

  - defense in depth: even if a ChatHistory row somehow ended up with an
    unwhitelisted key in `inputs` (bypassing the write-time whitelist in
    accounts.history_input_schema.sanitize_inputs through some other
    write path -- a fixture, a future code path, a data migration),
    the READ-time restoration in AI_GENERATORS.views.create() still only
    ever restores whitelisted dom ids -- two independent whitelist
    checks, not one
  - the new dashboard/save_history `inputs` surface still requires
    login and (for save_history) CSRF -- unchanged decorators, verified
    directly rather than assumed
  - a value containing markup is never restorable as markup -- it can
    only ever reach a dom_id via `.value = ...` (verified in
    test_generator_lifecycle-style source assertions elsewhere); here
    we confirm the value itself survives whitelisting completely
    unmodified (no escaping/mangling that could itself be exploited)
    since json_script is what carries the escaping duty, not this layer
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ChatHistory


class DefenseInDepthOnReopenTests(TestCase):
    """
    Simulates a ChatHistory.inputs dict that bypassed the write-time
    whitelist (e.g. written directly via the ORM, as a fixture/migration
    might) to prove the read-time restoration path in
    AI_GENERATORS.views.create() enforces its own, independent
    whitelist rather than trusting whatever is already in the database.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="secuser", password="pw12345!")
        self.client.force_login(self.user)

    def test_unwhitelisted_stored_keys_are_never_restored(self):
        item = ChatHistory.objects.create(
            user=self.user,
            query="Application: leak test",
            response="r",
            feature="application",
            inputs={
                "purpose": "legit value",
                # None of these are in GENERATOR_INPUT_FIELDS["application"]
                # -- simulating a row that somehow bypassed sanitize_inputs.
                "is_staff": True,
                "__proto__": "polluted",
                "credit_balance": 999999,
                "<script>alert(1)</script>": "x",
            },
        )
        response = self.client.get(
            reverse("home") + f"?generator=application&reopen={item.id}"
        )
        restored = response.context["restored_inputs"]
        self.assertEqual(restored, {"applicationPurpose": "legit value"})
        # Every restored key must be one of this feature's real dom ids.
        for dom_id in restored:
            self.assertIn(dom_id, {"applicantName", "applicationPurpose", "recipientName", "applicationReason", "applicationDetails", "applicationType"})

    def test_value_reaches_context_unmodified_for_json_script_to_escape(self):
        # json_script (Django) is the layer responsible for HTML-escaping
        # this value when it's serialized into the page -- confirming
        # this layer doesn't itself mangle/strip it (which could create
        # a false sense of safety or double-encode it).
        raw_value = '<img src=x onerror=alert(1)> & "quotes" \' apostrophes'
        item = ChatHistory.objects.create(
            user=self.user,
            query="Photo Prompt: x",
            response="r",
            feature="photo",
            inputs={"prompt": raw_value},
        )
        response = self.client.get(reverse("home") + f"?generator=photo&reopen={item.id}")
        self.assertEqual(response.context["restored_inputs"]["imagePrompt"], raw_value)
        # The page itself must never contain the raw, unescaped payload
        # (json_script HTML-escapes it into the <script type="application/json"> block).
        html = response.content.decode()
        self.assertNotIn("<img src=x onerror=alert(1)>", html)


class NewSurfaceAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="authuser", password="pw12345!")

    def test_save_history_requires_login(self):
        response = self.client.post(
            reverse("save_history"),
            data=json.dumps({"query": "q", "response": "r", "feature": "photo"}),
            content_type="application/json",
        )
        # login_required redirects anonymous requests, never processes them.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChatHistory.objects.count(), 0)

    def test_save_history_requires_csrf_token_for_session_auth(self):
        # A CSRF-unaware client (default Django test Client sends no
        # token and enforce_csrf_checks defaults off) still doesn't
        # prove much; explicitly turn CSRF enforcement on to confirm the
        # endpoint hasn't been (accidentally or otherwise) exempted.
        from django.test import Client

        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.user)
        response = strict_client.post(
            reverse("save_history"),
            data=json.dumps({"query": "q", "response": "r", "feature": "photo"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ChatHistory.objects.count(), 0)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
