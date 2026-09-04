"""
PHASE 9 — Step 8 (media/upload security): PIL.Image.DecompressionBombError
is a plain Exception subclass, not an OSError/ValueError, so
profile_avatar()'s validation `except (UnidentifiedImageError, OSError,
ValueError)` never actually caught it -- an image whose declared pixel
dimensions exceed Pillow's MAX_IMAGE_PIXELS guard would raise straight
through the view and 500 instead of returning the same clean 400 every
other invalid-image case gets. Fixed by adding DecompressionBombError to
the caught tuple.

This test lowers PIL's MAX_IMAGE_PIXELS threshold for the duration of the
test (rather than constructing a real multi-hundred-megapixel image) so a
small, fast, real image reliably triggers the actual guard Pillow itself
uses in production -- this exercises the real exception type, not a mock.
"""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

User = get_user_model()


class AvatarDecompressionBombTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bombuser@example.com",
            email="bombuser@example.com",
            password="pass12345",
        )
        self.client.force_login(self.user)

    def test_oversized_declared_pixel_count_returns_clean_400_not_500(self):
        buf = io.BytesIO()
        # 100x100 = 10,000 px -- ordinary and tiny by any real standard.
        Image.new("RGB", (100, 100), color="blue").save(buf, format="PNG")
        upload = SimpleUploadedFile("avatar.png", buf.getvalue(), content_type="image/png")

        original_max = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 100  # force the real DecompressionBombError path
        try:
            resp = self.client.post("/profile/avatar/", {"avatar": upload})
        finally:
            Image.MAX_IMAGE_PIXELS = original_max

        self.assertEqual(resp.status_code, 400)
        self.assertIn("valid image", resp.json().get("error", ""))
