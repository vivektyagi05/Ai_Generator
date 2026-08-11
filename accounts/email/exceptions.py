"""
Email Exceptions — Production-grade exception hierarchy for the email
infrastructure.

Every failure mode gets its own exception class so callers (and logs) can
distinguish transient problems (worth retrying) from permanent ones
(not worth retrying, and safe to surface as a clean error to the frontend).

Views must never leak these exceptions' internal details to the frontend —
only the generic message defined on each class should be shown.
"""


class EmailServiceError(Exception):
    """Base class for all email-infrastructure errors."""

    #: Generic, user-safe message. Never includes provider internals.
    public_message = "We couldn't send this email right now. Please try again shortly."

    #: Whether the retry system should retry this failure.
    retryable = False


class ConfigurationError(EmailServiceError, EnvironmentError):
    """Raised when required configuration (API key, sender, provider) is missing.

    Also inherits from the builtin EnvironmentError so any existing or future
    ``except EnvironmentError`` handling elsewhere in the project keeps working.
    """

    public_message = "Email service is temporarily unavailable."
    retryable = False


class InvalidEmailError(EmailServiceError):
    """Raised when the recipient email address fails validation."""

    public_message = "The email address provided is not valid."
    retryable = False


class ProviderRequestError(EmailServiceError):
    """Raised for a Brevo 400 that is NOT clearly about the recipient address
    (e.g. a malformed/missing field unrelated to `to`, a sender/template
    problem, or a 400 body we can't confidently classify).

    Kept distinct from InvalidEmailError so we never tell a user their email
    address is invalid when the real problem is a request/configuration
    issue on our side.
    """

    public_message = "We couldn't send this email right now. Please try again shortly."
    retryable = False


class TemplateRenderError(EmailServiceError):
    """Raised when an email template fails to render."""

    public_message = "We couldn't prepare this email. Please try again shortly."
    retryable = False


class AuthenticationFailedError(EmailServiceError):
    """Raised when the email provider rejects our API credentials (401/403)."""

    public_message = "Email service is temporarily unavailable."
    retryable = False


class RateLimitError(EmailServiceError):
    """Raised when the email provider's rate limit is hit (429)."""

    public_message = "Too many emails are being sent right now. Please try again shortly."
    retryable = True


class NetworkTimeoutError(EmailServiceError):
    """Raised when the request to the provider times out or the network is unreachable."""

    public_message = "We couldn't send this email right now. Please try again shortly."
    retryable = True


class ProviderUnavailableError(EmailServiceError):
    """Raised when the provider responds with a server-side (5xx) error."""

    public_message = "We couldn't send this email right now. Please try again shortly."
    retryable = True


class UnexpectedProviderResponseError(EmailServiceError):
    """Raised for a provider response that doesn't match any known/documented
    status code and therefore cannot be safely classified.

    Deliberately NOT retryable: an unrecognized status is far more likely to
    indicate a permanent request/config problem (or an undocumented Brevo
    behavior change) than a transient one, so blindly retrying it risks a
    retry storm against something that will never succeed.
    """

    public_message = "We couldn't send this email right now. Please try again shortly."
    retryable = False
