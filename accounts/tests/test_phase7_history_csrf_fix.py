"""
PHASE 7 GOLDEN UI / security sweep (broadened per checkpoint): audited
every POST-form endpoint on the 3 newly-migrated pages
(profile/history/plans) plus a full-codebase sweep for the same
pattern. Found one real, confirmed-exploitable gap:

`delete_history` and `clear_history` had NO method restriction --
reachable via a plain GET. Django's CsrfViewMiddleware only checks
unsafe-by-convention methods (POST/PUT/PATCH/DELETE); GET is exempt by
design because it's supposed to be safe/idempotent. That meant a
third-party page could delete a logged-in visitor's history with
nothing more than `<img src="https://this-app.com/history/delete/1/">`
or a plain link -- no form, no CSRF token, no user interaction beyond
the page loading. Confirmed exploitable before the fix: a bare
`self.client.get(...)` from an authenticated session deleted the row.

Fixed with `@require_POST`. The templates already only ever call these
via real `<form method="POST">` with `{% csrf_token %}`, so this closes
an unintended second path with no effect on any legitimate caller.

The rest of the sweep (profile_update, profile_change_password,
profile_delete, profile_avatar, and every mutating view in
AI_GENERATORS/api_views.py, accounts/billing_views.py,
accounts/entitlement_views.py, accounts/subscription_views.py,
accounts/webhook_views.py) was checked programmatically for the same
pattern (a view with .save()/.delete()/.create()/set_password/
update_session_auth_hash and neither a require_POST-family decorator
nor a manual request.method check) and found already correct --
these two were the only gap.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import ChatHistory

User = get_user_model()


class HistoryMutationRequiresPostTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="csrfsweepuser", email="csrfsweep@example.com", password="pass12345"
        )
        self.client.force_login(self.user)
        self.item = ChatHistory.objects.create(
            user=self.user, query="q", response="r", feature="story"
        )

    def test_get_no_longer_deletes_a_history_item(self):
        resp = self.client.get(f"/history/delete/{self.item.id}/")
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(ChatHistory.objects.filter(id=self.item.id).exists())

    def test_post_still_deletes_a_history_item(self):
        # The real, legitimate path (history.html's <form method="POST">)
        # must still work exactly as before.
        resp = self.client.post(f"/history/delete/{self.item.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ChatHistory.objects.filter(id=self.item.id).exists())

    def test_get_no_longer_clears_history(self):
        resp = self.client.get("/history/clear/")
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(ChatHistory.objects.filter(id=self.item.id).exists())

    def test_post_still_clears_history(self):
        resp = self.client.post("/history/clear/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ChatHistory.objects.filter(user=self.user).exists())

    def test_delete_still_scoped_to_the_requesting_user_only(self):
        # Not a regression from this fix, but worth locking in alongside
        # it: ownership isolation on the real POST path.
        other = User.objects.create_user(
            username="otheruser", email="other@example.com", password="pass12345"
        )
        other_item = ChatHistory.objects.create(
            user=other, query="not yours", response="r", feature="story"
        )
        self.client.post(f"/history/delete/{other_item.id}/")
        self.assertTrue(ChatHistory.objects.filter(id=other_item.id).exists())
