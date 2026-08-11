"""
OTP Service — Shared issuance and verification logic for EmailOTP records,
used identically by the signup flow and the forgot-password flow so the two
don't drift (RC audit: "Signup OTP should behave consistently with
forgot-password OTP").

Handles:
    - Race-safe upsert of the one-active-OTP-per-(email, purpose) row
    - Expiry
    - Max verification attempts
    - Deletion after successful verification (no replay)
"""

from django.db import transaction
from django.utils import timezone

from .models import EmailOTP
from .otp_utils import generate_otp
from .rate_limit import OTP_VERIFY_MAX_ATTEMPTS

import hmac


def issue_otp(email: str, purpose: str) -> str:
    """
    Generate a new OTP and persist it as the single active OTP for
    (email, purpose), resetting attempts and the expiry clock.

    Race-safe: uses select_for_update + get_or_create against the DB-level
    UniqueConstraint on (email, purpose), so two concurrent requests for the
    same (email, purpose) converge on one row instead of racing.
    """
    otp_code = generate_otp()
    with transaction.atomic():
        record, created = EmailOTP.objects.select_for_update().get_or_create(
            email=email,
            purpose=purpose,
            defaults={"otp": otp_code},
        )
        if not created:
            record.otp = otp_code
            record.attempts = 0
            record.created_at = timezone.now()
            record.save(update_fields=["otp", "attempts", "created_at"])
    return otp_code


def verify_otp(email: str, purpose: str, submitted_otp: str):
    """
    Verify a submitted OTP against the stored record for (email, purpose).

    Returns a (ok: bool, error_message: str | None, status_code: int) tuple.
    On success the record is deleted (no replay). On expiry or exceeding
    the max attempt count, the record is also deleted so a fresh
    `issue_otp()` call is required — never left half-invalidated.
    """
    if not submitted_otp:
        return False, "Verification code is required.", 400

    try:
        record = EmailOTP.objects.get(email=email, purpose=purpose)
    except EmailOTP.DoesNotExist:
        return False, "Verification code not found or already used. Please request a new one.", 400

    if record.is_expired():
        record.delete()
        return False, "Verification code has expired. Please request a new one.", 400

    if record.attempts >= OTP_VERIFY_MAX_ATTEMPTS:
        record.delete()
        return False, "Too many incorrect attempts. Please request a new code.", 403

    # Constant-time comparison — both operands are short, fixed-length,
    # server-generated digit strings, so this is cheap and avoids any
    # timing side-channel on the comparison itself.
    if not hmac.compare_digest(str(record.otp), str(submitted_otp)):
        record.attempts += 1
        record.save(update_fields=["attempts"])
        return False, "Invalid verification code.", 400

    record.delete()
    return True, None, 200
