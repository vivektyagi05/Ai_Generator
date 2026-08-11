"""
Error Mapping — Centralized translation from EmailServiceError subclasses to
HTTP status codes and safe, user-facing JSON error bodies.

Views must never hand-roll their own "except EmailServiceError: return 502"
handling (that collapses every failure mode into one status, per RC-003).
Instead they call `email_error_response()` and return the result directly.
"""

from django.http import JsonResponse

from accounts.email.exceptions import (
    AuthenticationFailedError,
    ConfigurationError,
    EmailServiceError,
    InvalidEmailError,
    NetworkTimeoutError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitError,
    TemplateRenderError,
    UnexpectedProviderResponseError,
)

# Ordered most-specific-first: EmailServiceError subclasses are checked with
# isinstance(), so a mapping table keyed by exact type plus a fallback is
# sufficient here (no diamond inheritance among these classes).
_STATUS_BY_EXCEPTION = {
    ConfigurationError: 500,           # our config is broken — not the caller's fault
    AuthenticationFailedError: 503,    # provider rejected our credentials
    InvalidEmailError: 400,            # bad user input
    RateLimitError: 429,               # provider rate limit hit
    ProviderUnavailableError: 503,     # provider 5xx
    NetworkTimeoutError: 504,          # network/timeout reaching provider
    ProviderRequestError: 500,         # ambiguous/unclassified 400 — our problem, not the user's
    UnexpectedProviderResponseError: 502,  # genuinely unexpected provider behavior
    TemplateRenderError: 500,          # our template broke
}

_DEFAULT_STATUS = 502


def status_for(exc: EmailServiceError) -> int:
    """Return the correct HTTP status code for an EmailServiceError instance."""
    for exc_type, status in _STATUS_BY_EXCEPTION.items():
        if isinstance(exc, exc_type):
            return status
    return _DEFAULT_STATUS


def email_error_response(exc: EmailServiceError) -> JsonResponse:
    """
    Build a safe JsonResponse for an EmailServiceError.

    Only `exc.public_message` is ever exposed — never the raw exception
    message, provider response body, or exception class name.
    """
    return JsonResponse({"error": exc.public_message}, status=status_for(exc))
