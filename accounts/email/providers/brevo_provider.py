"""
BrevoProvider — Concrete EmailProvider adapter for the Brevo transactional
email API (https://api.brevo.com/v3/smtp/email).

Only this module knows about Brevo's request/response shape. Everything
above (EmailService, templates, views) is provider-agnostic.
"""

import logging

import requests

from accounts.email.providers.base import EmailProvider, EmailSendResult
from accounts.email.exceptions import (
    AuthenticationFailedError,
    InvalidEmailError,
    NetworkTimeoutError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitError,
    UnexpectedProviderResponseError,
)

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoProvider(EmailProvider):
    """Sends transactional email through Brevo's REST API."""

    def __init__(self, api_key: str, from_email: str, from_name: str, timeout_seconds: float):
        self._api_key = api_key
        self._from_email = from_email
        self._from_name = from_name
        self._timeout_seconds = timeout_seconds

    def send(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        request_id: str,
    ) -> EmailSendResult:
        payload = {
            "sender": {"name": self._from_name, "email": self._from_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_body,
        }
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = requests.post(
                BREVO_API_URL,
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except requests.exceptions.Timeout as exc:
            logger.warning(
                "Brevo request timed out",
                extra={"request_id": request_id, "to": to_email},
            )
            raise NetworkTimeoutError("Request to Brevo timed out.") from exc
        except requests.exceptions.ConnectionError as exc:
            logger.warning(
                "Brevo network unreachable",
                extra={"request_id": request_id, "to": to_email},
            )
            raise NetworkTimeoutError("Network unreachable while contacting Brevo.") from exc

        if response.status_code == 201:
            message_id = ""
            try:
                message_id = response.json().get("messageId", "")
            except ValueError:
                pass
            logger.info(
                "Brevo email accepted",
                extra={
                    "request_id": request_id,
                    "to": to_email,
                    "provider_message_id": message_id,
                    "status_code": response.status_code,
                },
            )
            return EmailSendResult(provider_message_id=message_id, provider_name="brevo")

        # ── Error mapping ─────────────────────────────────────────────────────
        # Brevo's documented error body shape is {"code": "...", "message": "..."}.
        # We only trust `code`/`message` for classification when it actually
        # parses — we never assume a status code alone tells us what went
        # wrong (see RC-003: a 400 is not always an invalid recipient).
        error_code = ""
        error_message = ""
        try:
            error_body = response.json()
            if isinstance(error_body, dict):
                error_code = str(error_body.get("code", "")).lower()
                error_message = str(error_body.get("message", ""))
        except ValueError:
            pass

        body_snippet = response.text[:300] if response.text else ""
        logger.warning(
            "Brevo email send failed",
            extra={
                "request_id": request_id,
                "to": to_email,
                "status_code": response.status_code,
                "provider_error_code": error_code,
                "provider_response": body_snippet,
            },
        )

        if response.status_code in (401, 403):
            raise AuthenticationFailedError("Brevo rejected the configured API key.")

        if response.status_code == 429:
            raise RateLimitError("Brevo rate limit exceeded.")

        if response.status_code == 400:
            # Brevo's documented invalid-parameter code, combined with a
            # message that actually mentions the recipient field, is the
            # only case we confidently attribute to the recipient address.
            # Everything else (missing sender, template problems, other
            # malformed fields) is a request/config issue, not a bad email.
            recipient_mentioned = "to" in error_message.lower() or "recipient" in error_message.lower()
            if error_code == "invalid_parameter" and recipient_mentioned:
                raise InvalidEmailError(f"Brevo rejected the recipient address: {to_email}")
            raise ProviderRequestError(
                f"Brevo rejected the request (400): code={error_code or 'unknown'}"
            )

        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Brevo server error: {response.status_code}")

        # Any other status Brevo isn't documented to return for this endpoint
        # is unclassifiable — fail closed (non-retryable) rather than
        # guessing, so we don't retry-storm something that will never work.
        raise UnexpectedProviderResponseError(
            f"Unexpected Brevo response: {response.status_code}"
        )
