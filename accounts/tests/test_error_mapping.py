from unittest import TestCase

from accounts.email.error_mapping import email_error_response, status_for
from accounts.email.exceptions import (
    AuthenticationFailedError,
    ConfigurationError,
    InvalidEmailError,
    NetworkTimeoutError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitError,
    TemplateRenderError,
    UnexpectedProviderResponseError,
)


class ErrorMappingTests(TestCase):
    def test_status_codes_are_distinct_not_blanket_502(self):
        cases = {
            ConfigurationError("x"): 500,
            AuthenticationFailedError("x"): 503,
            InvalidEmailError("x"): 400,
            RateLimitError("x"): 429,
            ProviderUnavailableError("x"): 503,
            NetworkTimeoutError("x"): 504,
            ProviderRequestError("x"): 500,
            UnexpectedProviderResponseError("x"): 502,
            TemplateRenderError("x"): 500,
        }
        seen_statuses = set()
        for exc, expected_status in cases.items():
            self.assertEqual(status_for(exc), expected_status, msg=type(exc).__name__)
            seen_statuses.add(expected_status)
        # Sanity: this used to collapse everything into 502 (RC-003) — make
        # sure the mapping is genuinely differentiated, not accidentally
        # uniform again.
        self.assertGreater(len(seen_statuses), 1)

    def test_response_body_never_leaks_internal_message(self):
        exc = AuthenticationFailedError("Brevo rejected API key ABCDEF-secret")
        response = email_error_response(exc)
        body = response.content.decode()
        self.assertNotIn("ABCDEF-secret", body)
        self.assertIn(AuthenticationFailedError.public_message, body)
