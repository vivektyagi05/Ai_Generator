"""
Retry System — Exponential backoff for transient email delivery failures.

Only exceptions marked `retryable = True` (network timeouts, provider 5xx,
rate limits) are retried. Permanent failures (invalid email, bad
credentials, template errors, missing config) fail fast on the first try.
"""

import logging
import time
from typing import Callable, TypeVar

from accounts.email.exceptions import EmailServiceError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def send_with_retry(
    send_fn: Callable[[], T],
    max_retries: int,
    base_delay_seconds: float,
    request_id: str,
) -> T:
    """
    Execute `send_fn` with exponential backoff on retryable failures.

    Args:
        send_fn: Zero-argument callable that performs the actual send attempt.
        max_retries: Maximum number of attempts (including the first).
        base_delay_seconds: Base delay for exponential backoff (delay = base * 2^attempt).
        request_id: Correlation ID for structured logging.

    Returns:
        Whatever `send_fn` returns on success.

    Raises:
        EmailServiceError: the last error encountered, once retries are exhausted
            or the error is non-retryable.
    """
    last_error: EmailServiceError = None

    for attempt in range(1, max_retries + 1):
        try:
            return send_fn()
        except EmailServiceError as exc:
            last_error = exc

            if not exc.retryable:
                logger.info(
                    "Email send failed with non-retryable error",
                    extra={"request_id": request_id, "attempt": attempt, "error": type(exc).__name__},
                )
                raise

            if attempt >= max_retries:
                logger.warning(
                    "Email send exhausted all retry attempts",
                    extra={"request_id": request_id, "attempt": attempt, "error": type(exc).__name__},
                )
                raise

            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.info(
                "Retrying email send after transient failure",
                extra={
                    "request_id": request_id,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "delay_seconds": delay,
                    "error": type(exc).__name__,
                },
            )
            time.sleep(delay)

    # Unreachable in practice, but keeps type-checkers happy.
    raise last_error
