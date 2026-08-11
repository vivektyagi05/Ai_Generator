"""
Email Validators — Address validation, header-injection prevention, and
input sanitization for the email infrastructure.
"""

import re

from accounts.email.exceptions import InvalidEmailError

# Pragmatic, RFC-5322-adjacent pattern — good enough to reject obvious
# malformed input without rejecting legitimate edge-case addresses.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Any of these characters in a value destined for an email header (To,
# Subject, From) indicates a header-injection attempt.
_HEADER_INJECTION_PATTERN = re.compile(r"[\r\n]")


def validate_email_address(email: str) -> str:
    """
    Validate and normalize a recipient email address.

    Raises:
        InvalidEmailError: if the address is malformed or contains
            characters that could be used for header injection.
    """
    if not email or not isinstance(email, str):
        raise InvalidEmailError("Email address is required.")

    candidate = email.strip()

    if _HEADER_INJECTION_PATTERN.search(candidate):
        raise InvalidEmailError("Email address contains invalid characters.")

    if not _EMAIL_PATTERN.match(candidate):
        raise InvalidEmailError(f"'{email}' is not a valid email address.")

    return candidate.lower()


def sanitize_header_value(value: str) -> str:
    """Strip characters that could be used to inject extra email headers."""
    if not value:
        return ""
    return _HEADER_INJECTION_PATTERN.sub("", value)
