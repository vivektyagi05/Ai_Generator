"""
Email Service — Public facade for AI Generators' email infrastructure.

This module is the ONLY thing accounts/views.py should ever import from
for sending email. It exposes small, purpose-named functions and hides
everything about Brevo, retries, and templates behind them.

    from accounts.email_service import send_registration_otp
    send_registration_otp(email, otp_code)

Internally, sending is now handled by a production-grade, provider-agnostic
architecture:

    EmailService -> Validation -> Template -> Retry -> Provider Adapter (Brevo)
        -> Brevo API -> Internet -> Inbox

See accounts/email/ for the implementation:
    - service.py       EmailService orchestrator (validation, retry, logging)
    - config.py        Environment-variable configuration loader
    - templates.py     HTML email templates (rendering only)
    - providers/       Provider adapters (currently: Brevo)
    - exceptions.py    Exception hierarchy
    - retry.py         Exponential-backoff retry system
    - validators.py    Email validation / header-injection prevention

Previously this module used Django's SMTP email backend (Gmail SMTP via
EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD in settings.py), which works
on localhost but fails on Render because outbound SMTP connections are
blocked there. That SMTP-based code path has been fully removed — nothing
in this project calls django.core.mail.send_mail anymore.
"""

import logging

from accounts.email.service import get_email_service
from accounts.email.exceptions import EmailServiceError  # noqa: F401  (re-exported for callers)

logger = logging.getLogger(__name__)


def send_registration_otp(email: str, otp_code: str) -> None:
    """
    Send a signup verification OTP email.

    Raises:
        EmailServiceError: if delivery fails after retries. Callers should
        catch this and show the user a friendly "couldn't send OTP, try
        again" response — OTP delivery is required for signup to proceed,
        so failures here must NOT be silently swallowed.
    """
    get_email_service().send_registration_otp(email, otp_code)


def send_reset_otp(email: str, otp_code: str) -> None:
    """
    Send a password reset OTP email.

    Raises:
        EmailServiceError: if delivery fails after retries (same reasoning
        as send_registration_otp — the user needs this code to proceed).
    """
    get_email_service().send_reset_otp(email, otp_code)


def send_welcome_email(email: str, name: str = "") -> None:
    """
    Send a welcome email after successful signup.

    Best-effort: never raises. A failure here must never break the signup
    flow, since the account has already been created.
    """
    try:
        get_email_service().send_welcome_email(email, name)
    except EmailServiceError:
        logger.exception("Welcome email failed to send to %s", email)


def send_password_changed_email(email: str) -> None:
    """
    Send a password-changed security notification.

    Best-effort: never raises. A failure here must never break the
    password-change/reset flow, since the password has already been updated.
    """
    try:
        get_email_service().send_password_changed_email(email)
    except EmailServiceError:
        logger.exception("Password-changed email failed to send to %s", email)
