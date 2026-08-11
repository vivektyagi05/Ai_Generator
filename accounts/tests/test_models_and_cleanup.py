from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailOTP, PendingSignup


class EmailOTPModelTests(TestCase):
    def test_is_expired_false_when_fresh(self):
        record = EmailOTP.objects.create(email="a@example.com", otp="123456", purpose="signup")
        self.assertFalse(record.is_expired())

    def test_is_expired_true_after_five_minutes(self):
        record = EmailOTP.objects.create(email="a@example.com", otp="123456", purpose="signup")
        record.created_at = timezone.now() - timedelta(minutes=6)
        record.save(update_fields=["created_at"])
        self.assertTrue(record.is_expired())

    def test_unique_constraint_on_email_purpose(self):
        EmailOTP.objects.create(email="a@example.com", otp="111111", purpose="signup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EmailOTP.objects.create(email="a@example.com", otp="222222", purpose="signup")

    def test_different_purpose_same_email_allowed(self):
        EmailOTP.objects.create(email="a@example.com", otp="111111", purpose="signup")
        EmailOTP.objects.create(email="a@example.com", otp="222222", purpose="reset")
        self.assertEqual(EmailOTP.objects.filter(email="a@example.com").count(), 2)


class PendingSignupModelTests(TestCase):
    def test_is_expired_false_when_fresh(self):
        record = PendingSignup.objects.create(email="a@example.com", name="A", password_hash="hash")
        self.assertFalse(record.is_expired())

    def test_is_expired_true_after_fifteen_minutes(self):
        record = PendingSignup.objects.create(email="a@example.com", name="A", password_hash="hash")
        record.created_at = timezone.now() - timedelta(minutes=16)
        record.save(update_fields=["created_at"])
        self.assertTrue(record.is_expired())


class CleanupExpiredOtpsCommandTests(TestCase):
    def test_deletes_only_expired_rows(self):
        fresh = EmailOTP.objects.create(email="fresh@example.com", otp="111111", purpose="signup")
        stale = EmailOTP.objects.create(email="stale@example.com", otp="222222", purpose="signup")
        stale.created_at = timezone.now() - timedelta(minutes=10)
        stale.save(update_fields=["created_at"])

        fresh_pending = PendingSignup.objects.create(email="fresh2@example.com", name="F", password_hash="h")
        stale_pending = PendingSignup.objects.create(email="stale2@example.com", name="S", password_hash="h")
        stale_pending.created_at = timezone.now() - timedelta(minutes=20)
        stale_pending.save(update_fields=["created_at"])

        out = StringIO()
        call_command("cleanup_expired_otps", stdout=out)

        self.assertTrue(EmailOTP.objects.filter(pk=fresh.pk).exists())
        self.assertFalse(EmailOTP.objects.filter(pk=stale.pk).exists())
        self.assertTrue(PendingSignup.objects.filter(pk=fresh_pending.pk).exists())
        self.assertFalse(PendingSignup.objects.filter(pk=stale_pending.pk).exists())

    def test_command_is_idempotent(self):
        call_command("cleanup_expired_otps", stdout=StringIO())
        # Running again with nothing expired should not error.
        call_command("cleanup_expired_otps", stdout=StringIO())
