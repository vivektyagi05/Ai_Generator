"""
PHASE 8 STEP 2/3 regression tests -- ChatHistory.inputs + reopen restoration.

Covers:
  - save_history() stores whitelisted structured inputs for a recognized
    generator feature
  - save_history() silently drops unrecognized keys (server-side
    whitelist, never trust client-supplied field names) instead of
    storing them
  - save_history() drops inputs entirely for a feature with no schema
    (e.g. "chat") or an unrecognized feature, rather than storing junk
  - long values are truncated to the schema's max length, not rejected
    outright or stored unbounded
  - AI_GENERATORS create() view: ?reopen=<id> restores the real stored
    inputs into the page context, scoped to the requesting user and to
    the matching feature -- this is the cross-user isolation boundary
  - a mismatched feature, another user's row, or a nonexistent id all
    restore nothing (fail closed, not an error page)
  - a legacy row (inputs == {}) falls back to the plain `query` text
    instead of restoring nothing or inventing fields
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.history_input_schema import sanitize_inputs
from accounts.models import ChatHistory


class SanitizeInputsTests(TestCase):
    def test_whitelisted_keys_pass_through(self):
        cleaned = sanitize_inputs("photo", {"prompt": "a cat in a hat"})
        self.assertEqual(cleaned, {"prompt": "a cat in a hat"})

    def test_unknown_keys_are_dropped(self):
        cleaned = sanitize_inputs(
            "photo", {"prompt": "ok", "is_admin": True, "credits": 999999}
        )
        self.assertEqual(cleaned, {"prompt": "ok"})

    def test_unknown_feature_returns_empty(self):
        self.assertEqual(sanitize_inputs("not_a_real_feature", {"prompt": "x"}), {})

    def test_feature_with_no_schema_returns_empty(self):
        # "chat" is a real ChatHistory.feature value but has no generator
        # form to restore into.
        self.assertEqual(sanitize_inputs("chat", {"anything": "x"}), {})

    def test_non_dict_inputs_returns_empty(self):
        self.assertEqual(sanitize_inputs("photo", "not a dict"), {})
        self.assertEqual(sanitize_inputs("photo", None), {})
        self.assertEqual(sanitize_inputs("photo", ["a", "list"]), {})

    def test_long_values_are_truncated_not_rejected(self):
        cleaned = sanitize_inputs("photo", {"prompt": "x" * 10000})
        self.assertEqual(len(cleaned["prompt"]), 4000)

    def test_blank_values_are_dropped(self):
        cleaned = sanitize_inputs("application", {"purpose": "  ", "name": "Vivek"})
        self.assertEqual(cleaned, {"name": "Vivek"})


class SaveHistoryInputsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_structured_inputs_are_stored_for_recognized_feature(self):
        self.client.post(
            reverse("save_history"),
            data=json.dumps(
                {
                    "query": "Application: leave",
                    "response": "Dear Sir...",
                    "feature": "application",
                    "inputs": {
                        "type": "Leave",
                        "name": "Vivek Kumar",
                        "purpose": "leave",
                        "recipient": "Principal",
                        "reason": "fever",
                        "details": "",
                    },
                }
            ),
            content_type="application/json",
        )
        item = ChatHistory.objects.get()
        self.assertEqual(item.inputs["name"], "Vivek Kumar")
        self.assertEqual(item.inputs["reason"], "fever")
        self.assertNotIn("details", item.inputs)  # blank values dropped

    def test_unknown_input_keys_never_reach_the_database(self):
        self.client.post(
            reverse("save_history"),
            data=json.dumps(
                {
                    "query": "Photo Prompt: cat",
                    "response": "http://example.com/x.png",
                    "feature": "photo",
                    "inputs": {"prompt": "cat", "user_id": 99999, "is_staff": True},
                }
            ),
            content_type="application/json",
        )
        item = ChatHistory.objects.get()
        self.assertEqual(item.inputs, {"prompt": "cat"})

    def test_missing_inputs_key_stores_empty_dict_not_an_error(self):
        response = self.client.post(
            reverse("save_history"),
            data=json.dumps({"query": "q", "response": "r", "feature": "story"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        item = ChatHistory.objects.get()
        self.assertEqual(item.inputs, {})


class ReopenRestorationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner@example.com", password="pw12345!")
        self.other = User.objects.create_user(username="other@example.com", password="pw12345!")
        self.item = ChatHistory.objects.create(
            user=self.owner,
            query="Application: leave",
            response="Dear Sir...",
            feature="application",
            inputs={"name": "Vivek Kumar", "purpose": "leave", "reason": "fever"},
        )

    def _get_home(self, generator="application", reopen=None):
        url = reverse("home") + f"?generator={generator}"
        if reopen is not None:
            url += f"&reopen={reopen}"
        return self.client.get(url)

    def test_owner_reopening_own_row_restores_inputs(self):
        self.client.force_login(self.owner)
        response = self._get_home(reopen=self.item.id)
        self.assertEqual(response.status_code, 200)
        restored = response.context["restored_inputs"]
        self.assertEqual(restored["applicantName"], "Vivek Kumar")
        self.assertEqual(restored["applicationPurpose"], "leave")
        self.assertEqual(restored["applicationReason"], "fever")

    def test_other_user_cannot_restore_someone_elses_row(self):
        self.client.force_login(self.other)
        response = self._get_home(reopen=self.item.id)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["restored_inputs"])
        self.assertIsNone(response.context["restored_query_fallback"])

    def test_mismatched_feature_restores_nothing(self):
        self.client.force_login(self.owner)
        # The row is feature="application"; asking to open it as "proposal"
        # must not leak application fields into the proposal form.
        response = self._get_home(generator="proposal", reopen=self.item.id)
        self.assertIsNone(response.context["restored_inputs"])

    def test_nonexistent_id_restores_nothing_not_an_error(self):
        self.client.force_login(self.owner)
        response = self._get_home(reopen=999999)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["restored_inputs"])

    def test_non_numeric_reopen_id_is_handled_gracefully(self):
        self.client.force_login(self.owner)
        response = self._get_home(reopen="<script>alert(1)</script>")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["restored_inputs"])

    def test_legacy_row_without_inputs_falls_back_to_query_text(self):
        legacy = ChatHistory.objects.create(
            user=self.owner,
            query="Application: old style row",
            response="...",
            feature="application",
            inputs={},
        )
        self.client.force_login(self.owner)
        response = self._get_home(reopen=legacy.id)
        self.assertIsNone(response.context["restored_inputs"])
        self.assertEqual(
            response.context["restored_query_fallback"], "Application: old style row"
        )

    def test_anonymous_user_gets_no_restoration(self):
        # No force_login: an anonymous visitor hitting a reopen link
        # (e.g. a stale/shared URL) gets the normal logged-out page, not
        # a lookup against request.user.
        response = self._get_home(reopen=self.item.id)
        self.assertIsNone(response.context["restored_inputs"])
