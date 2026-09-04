"""
PHASE 9 — Step 3/8 (media cleanup on account deletion): UserProfile.avatar
is a real file in storage; Django never deletes the underlying file for
you on its own. Before this fix, every account deletion where an avatar
had ever been uploaded left an orphaned file under media/avatars/ with no
DB row pointing at it. Fixed by explicitly deleting the avatar from
storage as part of account_deletion_service.delete_account() (see Step 7:
that service anonymizes + deactivates the account rather than hard-
deleting the User row, but the avatar file itself is still real personal
data and is still actually removed from storage either way).
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from accounts.models import UserProfile

User = get_user_model()


def _tiny_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color="red").save(buf, format="PNG")
    return buf.getvalue()


class ProfileDeleteAvatarCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avatardeluser@example.com",
            email="avatardeluser@example.com",
            password="pass12345",
        )
        self.client.force_login(self.user)

    def test_avatar_file_removed_from_storage_on_account_deletion(self):
        upload = SimpleUploadedFile("avatar.png", _tiny_png_bytes(), content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 200)

        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.avatar)
        storage = profile.avatar.storage
        stored_name = profile.avatar.name
        self.assertTrue(storage.exists(stored_name))

        resp = self.client.post(
            "/profile/delete/",
            data='{"password":"pass12345"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        self.assertFalse(User.objects.filter(username="avatardeluser@example.com").exists())
        self.assertFalse(storage.exists(stored_name))

    def test_account_deletion_still_works_with_no_avatar_ever_set(self):
        resp = self.client.post(
            "/profile/delete/",
            data='{"password":"pass12345"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="avatardeluser@example.com").exists())
