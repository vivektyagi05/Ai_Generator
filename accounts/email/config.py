"""
Email Configuration Loader — Loads and validates all email-related
environment variables in one place.

Never hardcode secrets. Every value here comes from the environment
(populated by .env locally via python-dotenv in AI_GENERATORS/settings.py,
or Render's environment settings in production).
"""

import os
from dataclasses import dataclass

from accounts.email.exceptions import ConfigurationError


@dataclass(frozen=True)
class EmailConfig:
    """Immutable, validated email configuration for the active provider."""

    provider: str
    api_key: str
    from_email: str
    from_name: str
    request_timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0


def load_email_config() -> EmailConfig:
    """
    Load email configuration from environment variables.

    Required environment variables:
        EMAIL_PROVIDER    — currently only "brevo" is supported
        BREVO_API_KEY     — API key for the Brevo transactional email API
        EMAIL_FROM        — verified sender email address
        EMAIL_FROM_NAME   — display name for the sender

    Optional:
        EMAIL_TIMEOUT_SECONDS, EMAIL_MAX_RETRIES, EMAIL_RETRY_BASE_DELAY

    Raises:
        ConfigurationError: if a required variable is missing.
    """
    provider = os.environ.get("EMAIL_PROVIDER", "brevo").strip().lower()

    if provider == "brevo":
        api_key = os.environ.get("BREVO_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "BREVO_API_KEY is not set. Configure it in your environment "
                "(Render dashboard or .env) before sending email."
            )
    else:
        raise ConfigurationError(
            f"Unsupported EMAIL_PROVIDER '{provider}'. Supported providers: brevo."
        )

    from_email = os.environ.get("EMAIL_FROM", "").strip()
    if not from_email:
        raise ConfigurationError(
            "EMAIL_FROM is not set. It must be a sender address verified with your "
            "email provider."
        )

    from_name = os.environ.get("EMAIL_FROM_NAME", "AI Generators").strip()

    return EmailConfig(
        provider=provider,
        api_key=api_key,
        from_email=from_email,
        from_name=from_name,
        request_timeout_seconds=float(os.environ.get("EMAIL_TIMEOUT_SECONDS", "10")),
        max_retries=int(os.environ.get("EMAIL_MAX_RETRIES", "3")),
        retry_base_delay_seconds=float(os.environ.get("EMAIL_RETRY_BASE_DELAY", "1")),
    )
