"""
EmailService — The single orchestration point for all outbound email.

    Authentication (views.py)
        -> EmailService Facade (email_service.py)
            -> EmailService (this module)
                -> Validation Layer
                    -> Template Layer
                        -> Retry Layer
                            -> Provider Adapter (Brevo)
                                -> Brevo API -> Internet -> Inbox

Callers (accounts/views.py) never touch a provider SDK or template markup
directly — they call the facade in email_service.py, which delegates here.
"""

import logging
import time
import uuid

from accounts.email import templates
from accounts.email.config import EmailConfig, load_email_config
from accounts.email.exceptions import EmailServiceError
from accounts.email.providers.base import EmailProvider
from accounts.email.providers.brevo_provider import BrevoProvider
from accounts.email.retry import send_with_retry
from accounts.email.validators import validate_email_address

logger = logging.getLogger(__name__)


def _build_provider(config: EmailConfig) -> EmailProvider:
    """Provider factory — the only place that maps a provider name to a class.

    Adding a future provider (e.g. Resend) means adding one `elif` branch
    here; nothing else in the codebase needs to change.
    """
    if config.provider == "brevo":
        return BrevoProvider(
            api_key=config.api_key,
            from_email=config.from_email,
            from_name=config.from_name,
            timeout_seconds=config.request_timeout_seconds,
        )
    raise EmailServiceError(f"No provider adapter registered for '{config.provider}'.")


class EmailService:
    """
    Production email service: validation -> template rendering -> retrying
    provider dispatch -> structured logging, for every outbound email.
    """

    def __init__(self, config: EmailConfig = None, provider: EmailProvider = None):
        """
        Dependency injection: config/provider can be supplied directly
        (e.g. for tests); otherwise they're loaded from the environment.
        """
        self._config = config or load_email_config()
        self._provider = provider or _build_provider(self._config)

    # ── Core dispatch ─────────────────────────────────────────────────────────

    def _dispatch(self, to_email: str, subject: str, html_body: str) -> None:
        request_id = uuid.uuid4().hex[:12]
        validated_email = validate_email_address(to_email)
        started_at = time.monotonic()

        def _attempt():
            return self._provider.send(
                to_email=validated_email,
                subject=subject,
                html_body=html_body,
                request_id=request_id,
            )

        try:
            result = send_with_retry(
                _attempt,
                max_retries=self._config.max_retries,
                base_delay_seconds=self._config.retry_base_delay_seconds,
                request_id=request_id,
            )
            latency_ms = round((time.monotonic() - started_at) * 1000, 1)
            logger.info(
                "Email sent",
                extra={
                    "request_id": request_id,
                    "to": validated_email,
                    "subject": subject,
                    "provider": result.provider_name,
                    "provider_message_id": result.provider_message_id,
                    "latency_ms": latency_ms,
                },
            )
        except EmailServiceError as exc:
            latency_ms = round((time.monotonic() - started_at) * 1000, 1)
            logger.error(
                "Email failed",
                extra={
                    "request_id": request_id,
                    "to": validated_email,
                    "subject": subject,
                    "error": type(exc).__name__,
                    "latency_ms": latency_ms,
                },
            )
            raise


    # ── Public API — one method per email type ───────────────────────────────

    def send_registration_otp(self, email: str, otp_code: str) -> None:
        """Send a signup verification OTP email."""
        html = templates.render_otp_email(
            otp_code=otp_code,
            heading="Verify Your Email Address",
            sub_heading=(
                "Welcome to Ai_Generator.com! Enter the code below to verify "
                "your email address and create your account."
            ),
        )
        self._dispatch(email, "Your Ai_Generator.com Verification Code", html)

    def send_reset_otp(self, email: str, otp_code: str) -> None:
        """Send a password reset OTP email."""
        html = templates.render_otp_email(
            otp_code=otp_code,
            heading="Reset Your Password",
            sub_heading=(
                "We received a request to reset your Ai_Generator.com password. "
                "Use the code below to proceed. If you didn't request this, ignore this email."
            ),
        )
        self._dispatch(email, "Your Ai_Generator.com Password Reset Code", html)

    def send_welcome_email(self, email: str, name: str = "") -> None:
        """Send a welcome email right after successful signup."""
        html = templates.render_welcome_email(name, email)
        self._dispatch(email, "Welcome to Ai_Generator.com", html)

    def send_password_changed_email(self, email: str) -> None:
        """Send a security notification after a password change."""
        html = templates.render_password_changed_email(email)
        self._dispatch(email, "Your Ai_Generator.com Password Was Changed", html)


# ── Lazy singleton ───────────────────────────────────────────────────────────
#
# Built on first use (not at import/Django-app-load time) so a missing
# EMAIL_PROVIDER/API key only breaks the request that actually needs to send
# an email, not Django startup or `manage.py` commands like migrate.

_instance: EmailService = None


def get_email_service() -> EmailService:
    """Return the process-wide EmailService instance, building it on first use."""
    global _instance
    if _instance is None:
        _instance = EmailService()
    return _instance
