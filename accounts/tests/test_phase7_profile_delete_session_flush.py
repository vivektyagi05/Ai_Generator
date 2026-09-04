"""
PHASE 7 backend feature-matrix re-audit (profile.html's 4 account-
mutating actions: update, avatar, change-password, delete): confirmed
`profile_update`/`profile_change_password`/`profile_delete` all match
their frontend's field names correctly and were already covered by
pre-existing tests. One real bug found in `profile_delete`: the session
was never flushed after deleting the account, leaving a stale Session
row in the DB referencing a user that no longer exists (confirmed by
directly checking the sessions table before/after). Fixed with
`request.session.flush()`.

PHASE 9 — Step 7: `profile_delete` no longer hard-deletes the User row
(see accounts/services/account_deletion_service.py) -- it anonymizes +
deactivates instead, so the account-under-its-original-username assertion
below changed from "row is gone" to "row survives, anonymized,
is_active=False". The session-flush behavior itself is unchanged and
still covered here.
"""

from django.contrib.sessions.models import Session
from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class ProfileDeleteSessionFlushTests(TestCase):
    def test_session_is_flushed_after_account_deletion(self):
        user = User.objects.create_user(
            username="sessionflushuser", email="sessionflush@example.com", password="pass12345"
        )
        self.client.force_login(user)
        session_key = self.client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())

        resp = self.client.post(
            "/profile/delete/",
            data='{"password":"pass12345"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        # No longer gone -- anonymized + deactivated. See
        # test_phase9_account_deletion_retention.py for the full retention
        # behavior; this file stays focused on session flushing.
        self.assertFalse(User.objects.filter(username="sessionflushuser").exists())
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        # The old session key must no longer resolve to a live session
        # row -- flush() both deletes the stored session data and
        # rotates the session key.
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_wrong_password_does_not_delete_the_account_or_flush_the_session(self):
        user = User.objects.create_user(
            username="wrongpwuser", email="wrongpw@example.com", password="pass12345"
        )
        self.client.force_login(user)
        session_key = self.client.session.session_key

        resp = self.client.post(
            "/profile/delete/",
            data='{"password":"totally-wrong"}',
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertTrue(User.objects.filter(username="wrongpwuser").exists())
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())
