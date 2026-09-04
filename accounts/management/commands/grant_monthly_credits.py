"""
management command: grant_monthly_credits

Grants accounts.credit_config.MONTHLY_FREE_CREDITS to every CreditAccount
that hasn't already received it for the current calendar month
(credit_service.grant_monthly_credits() is idempotent per account per
period, and is additionally backstopped by a DB-level uniqueness
constraint on the ledger — see accounts/models.py CreditTransaction.Meta).

Usage:
    python manage.py grant_monthly_credits

Production scheduling: same situation as cleanup_expired_otps (see that
command's docstring) — this project has no Celery/cron worker of its own.
Trigger this from whatever scheduler the deployment platform offers (e.g.
a Render Cron Job) running once a month. This command is infrastructure
only, per Phase 1 Step 10 — it is NOT wired to any automatic schedule by
this phase.
"""

from django.core.management.base import BaseCommand

from accounts.models import CreditAccount
from accounts.services import credit_service


class Command(BaseCommand):
    help = "Grant the configured monthly free credit amount to every CreditAccount that hasn't received it yet this period."

    def handle(self, *args, **options):
        granted = 0
        skipped = 0

        for account in CreditAccount.objects.all().iterator():
            txn = credit_service.grant_monthly_credits(account)
            if txn is not None:
                granted += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Monthly grant complete: {granted} account(s) granted, "
                f"{skipped} account(s) already had this period's grant."
            )
        )
