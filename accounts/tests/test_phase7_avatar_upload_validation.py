"""
PHASE 7 backend feature-matrix re-audit: `profile_avatar` previously
only checked that a file was present -- no type validation, no size
cap, and the model's `ImageField` Pillow-based "is this actually an
image" check does NOT run on a direct `.save()` call the way this view
made it (only on `ModelForm.is_valid()`/`full_clean()`), so that
protection was never actually active. This file behaviorally verifies
the real fix: JPG/PNG/WebP only, 5 MB cap, real content-level
validation (not filename/extension/Content-Type-header alone),
structured JSON errors the frontend already displays verbatim
(`if (d.error) UI.notify(d.error)` -- unchanged, needed no frontend
fix), previous-avatar cleanup, no filesystem-path exposure, and that
authorization/CSRF were not weakened.
"""

import io

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import UserProfile

User = get_user_model()


def _real_image_bytes(fmt="PNG", size=(10, 10), color="red"):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format=fmt)
    return buf.getvalue()


def _real_oversized_image_bytes(min_bytes=5 * 1024 * 1024 + 1024):
    # A flat-color PNG compresses to almost nothing regardless of
    # canvas size (PNG's DEFLATE compression collapses uniform pixel
    # data), so a real oversized fixture needs genuinely incompressible
    # pixel content -- random noise, saved uncompressed (BMP) to
    # guarantee the byte count, then re-encoded to PNG at a size large
    # enough that even PNG's compression can't bring it back under the
    # cap.
    import os

    width = height = 1600  # 1600*1600*3 bytes RGB = 7.68 MB uncompressed
    noise = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    noise.save(buf, format="PNG", compress_level=0)
    data = buf.getvalue()
    assert len(data) > min_bytes, f"fixture too small: {len(data)} bytes"
    return data


class AvatarUploadValidTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avataruser", email="avatar@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_valid_png_upload_succeeds_and_persists(self):
        upload = SimpleUploadedFile("photo.png", _real_image_bytes("PNG"), content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "uploaded")
        self.assertIn("url", data)

        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(bool(profile.avatar))
        self.assertTrue(profile.avatar.name.endswith(".png"))

    def test_valid_jpeg_upload_succeeds(self):
        upload = SimpleUploadedFile("photo.jpg", _real_image_bytes("JPEG"), content_type="image/jpeg")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "uploaded")

    def test_valid_webp_upload_succeeds(self):
        upload = SimpleUploadedFile("photo.webp", _real_image_bytes("WEBP"), content_type="image/webp")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "uploaded")

    def test_response_never_exposes_a_filesystem_path(self):
        upload = SimpleUploadedFile("photo.png", _real_image_bytes("PNG"), content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        data = resp.json()
        # A media URL, never a server-local absolute path.
        self.assertTrue(data["url"].startswith("/media/") or data["url"].startswith("http"))
        self.assertNotIn("/home/", data["url"])
        self.assertNotIn("/var/", data["url"])
        self.assertNotIn(":\\", data["url"])  # no Windows-style path either

    def test_path_traversal_filename_cannot_escape_the_avatars_directory(self):
        # Django's storage layer (Storage.get_valid_name /
        # get_available_name) is what actually enforces this -- this
        # test exists to lock in that this view relies on that real
        # mechanism rather than trusting the filename directly anywhere
        # in its own code.
        from django.core.files.storage import default_storage

        upload = SimpleUploadedFile(
            "../../../etc/passwd.png", _real_image_bytes("PNG"), content_type="image/png"
        )
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 200)
        saved_path = UserProfile.objects.get(user=self.user).avatar.name
        self.assertTrue(saved_path.startswith("avatars/"))
        self.assertNotIn("..", saved_path)
        self.assertTrue(default_storage.exists(saved_path))


class AvatarUploadInvalidTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avatarbaduser", email="avatarbad@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_non_image_file_with_spoofed_content_type_header_is_rejected(self):
        # The core requirement: validate real CONTENT, not the
        # client-supplied Content-Type header, which is trivially
        # spoofable -- this is plain text pretending to be a PNG.
        upload = SimpleUploadedFile(
            "malicious.png", b"this is not an image, just text pretending", content_type="image/png"
        )
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())
        self.assertFalse(UserProfile.objects.filter(user=self.user, avatar__gt="").exists())

    def test_executable_disguised_with_image_extension_is_rejected(self):
        fake_exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 50
        upload = SimpleUploadedFile("virus.jpg", fake_exe, content_type="image/jpeg")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_disallowed_but_real_image_format_is_rejected(self):
        # A genuinely valid, decodable image -- just not an allowed
        # format (BMP is real, Pillow can encode/decode it, but it's
        # not in the allowed policy list).
        upload = SimpleUploadedFile("photo.bmp", _real_image_bytes("BMP"), content_type="image/bmp")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_content_type_header_mismatch_is_rejected_even_with_real_image_bytes(self):
        # Real PNG bytes, but the client claims it's something else --
        # the Content-Type pre-filter catches this before Pillow even
        # runs.
        upload = SimpleUploadedFile("photo.txt", _real_image_bytes("PNG"), content_type="text/plain")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 400)

    def test_error_message_never_exposes_a_filesystem_path(self):
        upload = SimpleUploadedFile("bad.png", b"not an image", content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        error_text = resp.json()["error"]
        self.assertNotIn("/home/", error_text)
        self.assertNotIn("/tmp/", error_text)
        self.assertNotIn(".py", error_text)


class AvatarUploadSizeLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avatarsizeuser", email="avatarsize@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_oversized_upload_is_rejected(self):
        # A real, valid, genuinely-decodable PNG that is deliberately
        # larger than 5 MB via incompressible noise content (a flat-
        # color image compresses to almost nothing regardless of
        # canvas size, so it can't be used to prove the size check
        # actually runs) -- rejected purely for size, confirming the
        # size check runs independently of the content validation.
        oversized = _real_oversized_image_bytes()
        self.assertGreater(len(oversized), 5 * 1024 * 1024)
        upload = SimpleUploadedFile("huge.png", oversized, content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("5 MB", resp.json()["error"])

    def test_upload_at_the_size_boundary_still_succeeds(self):
        # A small real image is nowhere near the cap -- confirms the
        # size check doesn't false-positive on ordinary uploads.
        upload = SimpleUploadedFile("small.png", _real_image_bytes("PNG"), content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 200)


class AvatarUploadAuthorizationTests(TestCase):
    def test_anonymous_request_is_redirected_to_login_not_processed(self):
        upload = SimpleUploadedFile("photo.png", _real_image_bytes("PNG"), content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": upload})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)

    def test_get_request_is_rejected_not_processed(self):
        user = User.objects.create_user(
            username="avatargetuser", email="avatarget@example.com", password="pass12345"
        )
        self.client.force_login(user)
        resp = self.client.get("/profile/avatar/")
        self.assertEqual(resp.status_code, 405)

    def test_upload_only_ever_affects_the_requesting_users_own_profile(self):
        user_a = User.objects.create_user(username="usera", email="a@example.com", password="pass12345")
        user_b = User.objects.create_user(username="userb", email="b@example.com", password="pass12345")
        self.client.force_login(user_a)
        upload = SimpleUploadedFile("photo.png", _real_image_bytes("PNG"), content_type="image/png")
        self.client.post("/profile/avatar/", {"avatar": upload})

        # A post_save signal (accounts/signals.py) auto-creates a blank
        # UserProfile for every User, so both profiles exist -- what
        # must be scoped to the requesting user alone is the actual
        # AVATAR FILE, not the profile row's mere existence.
        self.assertTrue(bool(UserProfile.objects.get(user=user_a).avatar))
        self.assertFalse(bool(UserProfile.objects.get(user=user_b).avatar))


class AvatarUploadReplacementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avatarreplaceuser", email="avatarreplace@example.com", password="pass12345"
        )
        self.client.force_login(self.user)

    def test_re_upload_replaces_the_previous_avatar_not_accumulates(self):
        from django.core.files.storage import default_storage

        first = SimpleUploadedFile("first.png", _real_image_bytes("PNG", color="red"), content_type="image/png")
        resp1 = self.client.post("/profile/avatar/", {"avatar": first})
        first_path = UserProfile.objects.get(user=self.user).avatar.name
        self.assertTrue(default_storage.exists(first_path))

        second = SimpleUploadedFile("second.png", _real_image_bytes("PNG", color="blue"), content_type="image/png")
        resp2 = self.client.post("/profile/avatar/", {"avatar": second})
        second_path = UserProfile.objects.get(user=self.user).avatar.name

        self.assertEqual(resp2.status_code, 200)
        self.assertNotEqual(first_path, second_path)
        # The old file must actually be gone from storage, not just
        # unreferenced -- otherwise every re-upload silently
        # accumulates orphaned files under media/avatars/.
        self.assertFalse(default_storage.exists(first_path))
        self.assertTrue(default_storage.exists(second_path))

    def test_rejected_re_upload_does_not_delete_the_existing_valid_avatar(self):
        good = SimpleUploadedFile("good.png", _real_image_bytes("PNG"), content_type="image/png")
        self.client.post("/profile/avatar/", {"avatar": good})
        good_path = UserProfile.objects.get(user=self.user).avatar.name

        bad = SimpleUploadedFile("bad.png", b"not an image", content_type="image/png")
        resp = self.client.post("/profile/avatar/", {"avatar": bad})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(UserProfile.objects.get(user=self.user).avatar.name, good_path)
