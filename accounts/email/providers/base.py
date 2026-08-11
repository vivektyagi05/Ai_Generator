"""
EmailProvider Interface — Abstraction every concrete provider adapter must
implement.

This is what makes the provider swappable: EmailService only ever talks to
this interface, never to a specific vendor's SDK/API directly. Adding a new
provider (e.g. a future ResendProvider) means writing one new class here and
flipping EMAIL_PROVIDER — nothing else in the codebase changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailSendResult:
    """Normalized result returned by every provider adapter on success."""

    provider_message_id: str
    provider_name: str


class EmailProvider(ABC):
    """Common interface for all email-sending provider adapters."""

    @abstractmethod
    def send(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        request_id: str,
    ) -> EmailSendResult:
        """
        Send a single HTML email.

        Args:
            to_email: Recipient address (already validated).
            subject: Email subject line.
            html_body: Fully rendered HTML body.
            request_id: Correlation ID for structured logging.

        Returns:
            EmailSendResult on success.

        Raises:
            InvalidEmailError, AuthenticationFailedError, RateLimitError,
            NetworkTimeoutError, ProviderUnavailableError — see
            accounts.email.exceptions.
        """
        raise NotImplementedError
