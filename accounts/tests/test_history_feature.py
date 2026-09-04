"""
PHASE 5 STEP 4 regression tests -- ChatHistory.feature.

Covers:
  - save_history() stores a recognized `feature` verbatim
  - save_history() stores "" for a missing/unrecognized `feature`,
    never guesses one
  - history_view() derives display type from the stored feature when
    present, and falls back to the old content-sniffing heuristic
    (flagged is_legacy_classification=True) only for rows with no
    feature on record
  - the specific misclassification bug this replaces (a non-code result
    containing angle brackets) no longer happens for a properly-tagged
    row
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ChatHistory


class SaveHistoryFeatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_recognized_feature_is_stored_verbatim(self):
        self.client.post(
            reverse("save_history"),
            data=json.dumps({"query": "q", "response": "r", "feature": "story"}),
            content_type="application/json",
        )
        item = ChatHistory.objects.get()
        self.assertEqual(item.feature, "story")

    def test_missing_feature_stores_empty_not_a_guess(self):
        self.client.post(
            reverse("save_history"),
            data=json.dumps({"query": "q", "response": "r"}),
            content_type="application/json",
        )
        item = ChatHistory.objects.get()
        self.assertEqual(item.feature, "")

    def test_unrecognized_feature_stores_empty_not_the_raw_value(self):
        self.client.post(
            reverse("save_history"),
            data=json.dumps({"query": "q", "response": "r", "feature": "not_a_real_feature"}),
            content_type="application/json",
        )
        item = ChatHistory.objects.get()
        self.assertEqual(item.feature, "")


class HistoryViewTypeDerivationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u@example.com", password="pw12345!")
        self.client.force_login(self.user)

    def test_type_comes_from_feature_when_present(self):
        ChatHistory.objects.create(
            user=self.user, query="q", response="some <b>bold</b> proposal text", feature="proposal"
        )
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        # Regression: before this fix, a proposal containing "<" and ">"
        # was misclassified as "code" by the sniffing heuristic. Tagged
        # with its real feature, it's correctly "text" and not flagged
        # as a guess.
        self.assertEqual(item.type, "text")
        self.assertFalse(item.is_legacy_classification)

    def test_photo_feature_maps_to_image_type(self):
        ChatHistory.objects.create(
            user=self.user, query="q", response="https://example.com/x.png", feature="photo"
        )
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        self.assertEqual(item.type, "image")
        self.assertFalse(item.is_legacy_classification)

    def test_code_feature_maps_to_code_type(self):
        ChatHistory.objects.create(
            user=self.user, query="q", response="print('hi')", feature="code"
        )
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        self.assertEqual(item.type, "code")
        self.assertFalse(item.is_legacy_classification)

    def test_legacy_row_with_no_feature_falls_back_to_heuristic_and_is_flagged(self):
        # Simulates a row saved before this field existed.
        ChatHistory.objects.create(user=self.user, query="q", response="https://example.com/x.png", feature="")
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        self.assertEqual(item.type, "image")  # same heuristic as before
        self.assertTrue(item.is_legacy_classification)

    def test_legacy_row_misclassification_is_still_possible_and_flagged(self):
        # This is the exact pre-existing bug this change documents rather
        # than silently "fixes" for old data: a legacy row with no feature
        # on record, whose content happens to contain angle brackets, is
        # still sniffed as "code" -- but now it's visibly flagged as a
        # guess instead of presented as fact.
        ChatHistory.objects.create(
            user=self.user, query="q", response="Dear <Hiring Manager>, ...", feature=""
        )
        resp = self.client.get(reverse("history"))
        item = resp.context["history"][0]
        self.assertEqual(item.type, "code")
        self.assertTrue(item.is_legacy_classification)
