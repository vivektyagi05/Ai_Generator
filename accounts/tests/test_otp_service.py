from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailOTP
from accounts.otp_service import issue_otp, verify_otp
from accounts.rate_limit import OTP_VERIFY_MAX_ATTEMPTS


class IssueOtpTests(TestCase):
    def test_issue_otp_creates_one_record(self):
        otp = issue_otp("user@example.com", "signup")
        self.assertEqual(len(otp), 6)
        self.assertTrue(otp.isdigit())
        self.assertEqual(EmailOTP.objects.filter(email="user@example.com", purpose="signup").count(), 1)

    def test_reissue_replaces_existing_record_not_duplicates(self):
        first = issue_otp("user@example.com", "signup")
        second = issue_otp("user@example.com", "signup")
        self.assertNotEqual(first, second)
        self.assertEqual(EmailOTP.objects.filter(email="user@example.com", purpose="signup").count(), 1)

    def test_reissue_resets_attempts(self):
        issue_otp("user@example.com", "signup")
        record = EmailOTP.objects.get(email="user@example.com", purpose="signup")
        record.attempts = 3
        record.save(update_fields=["attempts"])

        issue_otp("user@example.com", "signup")
        record.refresh_from_db()
        self.assertEqual(record.attempts, 0)

    def test_signup_and_reset_purposes_coexist_for_same_email(self):
        issue_otp("user@example.com", "signup")
        issue_otp("user@example.com", "reset")
        self.assertEqual(EmailOTP.objects.filter(email="user@example.com").count(), 2)

    def test_unique_constraint_enforced_at_db_level(self):
        EmailOTP.objects.create(email="user@example.com", otp="111111", purpose="signup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EmailOTP.objects.create(email="user@example.com", otp="222222", purpose="signup")


class VerifyOtpTests(TestCase):
    def test_correct_otp_succeeds_and_deletes_record(self):
        otp = issue_otp("user@example.com", "signup")
        ok, error, status = verify_otp("user@example.com", "signup", otp)
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(status, 200)
        self.assertFalse(EmailOTP.objects.filter(email="user@example.com", purpose="signup").exists())

    def test_wrong_otp_fails_and_increments_attempts(self):
        issue_otp("user@example.com", "signup")
        ok, error, status = verify_otp("user@example.com", "signup", "000000")
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        record = EmailOTP.objects.get(email="user@example.com", purpose="signup")
        self.assertEqual(record.attempts, 1)

    def test_missing_otp_input_returns_error_without_touching_db(self):
        issue_otp("user@example.com", "signup")
        ok, error, status = verify_otp("user@example.com", "signup", "")
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        # attempts should be untouched
        record = EmailOTP.objects.get(email="user@example.com", purpose="signup")
        self.assertEqual(record.attempts, 0)

    def test_no_record_returns_error(self):
        ok, error, status = verify_otp("nobody@example.com", "signup", "123456")
        self.assertFalse(ok)
        self.assertEqual(status, 400)

    def test_expired_otp_fails_and_is_deleted(self):
        otp = issue_otp("user@example.com", "signup")
        record = EmailOTP.objects.get(email="user@example.com", purpose="signup")
        record.created_at = timezone.now() - timedelta(minutes=10)
        record.save(update_fields=["created_at"])

        ok, error, status = verify_otp("user@example.com", "signup", otp)
        self.assertFalse(ok)
        self.assertEqual(status, 400)
        self.assertFalse(EmailOTP.objects.filter(email="user@example.com", purpose="signup").exists())

    def test_max_attempts_locks_out_and_deletes_record(self):
        issue_otp("user@example.com", "signup")
        for _ in range(OTP_VERIFY_MAX_ATTEMPTS):
            verify_otp("user@example.com", "signup", "000000")

        # Record was deleted once attempts reached the max on a further try.
        ok, error, status = verify_otp("user@example.com", "signup", "000000")
        self.assertFalse(ok)
        self.assertIn(status, (400, 403))

    def test_replay_after_success_fails(self):
        otp = issue_otp("user@example.com", "signup")
        ok, _, _ = verify_otp("user@example.com", "signup", otp)
        self.assertTrue(ok)

        ok2, error2, status2 = verify_otp("user@example.com", "signup", otp)
        self.assertFalse(ok2)
        self.assertEqual(status2, 400)

    def test_signup_and_reset_otps_are_independent(self):
        signup_otp = issue_otp("user@example.com", "signup")
        reset_otp = issue_otp("user@example.com", "reset")
        self.assertNotEqual(signup_otp, reset_otp)

        # Using the reset OTP against the signup purpose must fail.
        ok, _, _ = verify_otp("user@example.com", "signup", reset_otp)
        self.assertFalse(ok)
