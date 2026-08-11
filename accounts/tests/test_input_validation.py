from django.test import TestCase, Client


class MissingFieldsDoNotCrashTests(TestCase):
    """
    Regression tests for the `request.POST.get("email").lower()` class of
    bug — calling a string method on a value that might be None must never
    reach the view code; every endpoint should return a clean 4xx/error
    response instead of an unhandled 500/AttributeError.
    """

    def setUp(self):
        self.client = Client()

    def test_login_with_no_body_does_not_500(self):
        response = self.client.post("/login/", {})
        self.assertNotEqual(response.status_code, 500)

    def test_login_with_only_email_does_not_500(self):
        response = self.client.post("/login/", {"email": "a@example.com"})
        self.assertNotEqual(response.status_code, 500)

    def test_signup_with_no_body_does_not_500(self):
        response = self.client.post("/signup/", {})
        self.assertNotEqual(response.status_code, 500)

    def test_signup_with_only_name_does_not_500(self):
        response = self.client.post("/signup/", {"name": "Ada"})
        self.assertNotEqual(response.status_code, 500)

    def test_verify_otp_with_no_body_does_not_500(self):
        response = self.client.post("/verify-otp/", {})
        self.assertNotEqual(response.status_code, 500)

    def test_forgot_send_otp_with_no_body_does_not_500(self):
        response = self.client.post("/forgot/send-otp/", {})
        self.assertNotEqual(response.status_code, 500)

    def test_forgot_verify_otp_with_no_body_does_not_500(self):
        response = self.client.post("/forgot/verify-otp/", {})
        self.assertNotEqual(response.status_code, 500)

    def test_forgot_reset_password_with_no_body_does_not_500(self):
        response = self.client.post("/forgot/reset-password/", {})
        self.assertNotEqual(response.status_code, 500)

    def test_resend_otp_with_no_session_does_not_500(self):
        response = self.client.post("/resend-otp/")
        self.assertNotEqual(response.status_code, 500)
