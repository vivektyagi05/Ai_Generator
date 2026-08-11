"""
management command: cleanup_expired_otps

Deletes expired EmailOTP rows and expired PendingSignup rows so they don't
accumulate forever. Safe to run repeatedly (idempotent) and cheap (two
bulk deletes).

Usage:
    python manage.py cleanup_expired_otps

Production scheduling: this project has no Celery/cron worker of its own,
so this is designed to be triggered by whatever scheduler the deployment
platform offers, e.g.:
    - Render: a Render "Cron Job" service running
      `python manage.py cleanup_expired_otps` on a schedule (e.g. every 15
      minutes) against the same environment/database as the web service.
    - Any host with cron: a crontab entry invoking this via `manage.py`
      with the project's virtualenv/interpreter.
See EMAIL_SETUP.md for the exact setup steps.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import EmailOTP, PendingSignup


class Command(BaseCommand):
    help = "Delete expired EmailOTP and PendingSignup records."

    def handle(self, *args, **options):
        from datetime import timedelta

        now = timezone.now()

        # Mirrors EmailOTP.is_expired() (5 min) / PendingSignup.is_expired()
        # (15 min) as DB-side cutoffs so we can bulk-delete without loading
        # every row into Python.
        otp_cutoff = now - timedelta(minutes=5)
        signup_cutoff = now - timedelta(minutes=15)

        deleted_otps, _ = EmailOTP.objects.filter(created_at__lt=otp_cutoff).delete()
        deleted_pending, _ = PendingSignup.objects.filter(created_at__lt=signup_cutoff).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_otps} expired EmailOTP row(s) and "
                f"{deleted_pending} expired PendingSignup row(s)."
            )
        )
