"""
BrevoProvider tests — mock only the outbound requests.post call. Everything
else (payload construction, header construction, status-code -> exception
mapping) runs for real.
"""

from unittest import TestCase
from unittest.mock import patch

import requests

from accounts.email.exceptions import (
    AuthenticationFailedError,
    InvalidEmailError,
    NetworkTimeoutError,
    ProviderRequestError,
    ProviderUnavailableError,
    RateLimitError,
    UnexpectedProviderResponseError,
)
from accounts.email.providers.brevo_provider import BrevoProvider, BREVO_API_URL
from accounts.tests.helpers import FakeResponse


def _provider():
    return BrevoProvider(
        api_key="test-api-key",
        from_email="noreply@example.com",
        from_name="Test Sender",
        timeout_seconds=5.0,
    )


class BrevoProviderTests(TestCase):
    def _send(self):
        return _provider().send(
            to_email="user@example.com",
            subject="Subject",
            html_body="<p>Body</p>",
            request_id="req-123",
        )

    # ── Success ──────────────────────────────────────────────────────────

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_success_201_with_message_id(self, mock_post):
        mock_post.return_value = FakeResponse(201, json_body={"messageId": "abc-123"})
        result = self._send()
        self.assertEqual(result.provider_message_id, "abc-123")
        self.assertEqual(result.provider_name, "brevo")

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_success_201_malformed_json_body(self, mock_post):
        """A 201 with a body that isn't valid JSON must still count as success."""
        response = FakeResponse(201, json_body=None, text="not json")
        mock_post.return_value = response
        result = self._send()
        self.assertEqual(result.provider_message_id, "")

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_request_payload_and_headers(self, mock_post):
        mock_post.return_value = FakeResponse(201, json_body={"messageId": "x"})
        self._send()
        _, kwargs = mock_post.call_args
        self.assertEqual(mock_post.call_args[0][0], BREVO_API_URL)
        self.assertEqual(kwargs["headers"]["api-key"], "test-api-key")
        self.assertEqual(kwargs["json"]["to"], [{"email": "user@example.com"}])
        self.assertEqual(kwargs["json"]["sender"]["email"], "noreply@example.com")
        self.assertEqual(kwargs["json"]["htmlContent"], "<p>Body</p>")
        self.assertEqual(kwargs["timeout"], 5.0)

    # ── Documented error statuses ────────────────────────────────────────

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_401_maps_to_authentication_failed(self, mock_post):
        mock_post.return_value = FakeResponse(401, json_body={"code": "unauthorized", "message": "Key not found"})
        with self.assertRaises(AuthenticationFailedError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_403_maps_to_authentication_failed(self, mock_post):
        mock_post.return_value = FakeResponse(403, json_body={"code": "unauthorized"})
        with self.assertRaises(AuthenticationFailedError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_429_maps_to_rate_limit(self, mock_post):
        mock_post.return_value = FakeResponse(429, json_body={"code": "too_many_requests"})
        with self.assertRaises(RateLimitError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_400_with_recipient_error_maps_to_invalid_email(self, mock_post):
        mock_post.return_value = FakeResponse(
            400, json_body={"code": "invalid_parameter", "message": "Invalid `to` recipient address"}
        )
        with self.assertRaises(InvalidEmailError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_400_sender_configuration_error_is_not_invalid_email(self, mock_post):
        """A 400 about the SENDER, not the recipient, must not be reported
        to the user as 'your email address is invalid' (RC-003)."""
        mock_post.return_value = FakeResponse(
            400, json_body={"code": "invalid_parameter", "message": "sender.email is not valid"}
        )
        with self.assertRaises(ProviderRequestError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_400_unclassifiable_body_is_not_invalid_email(self, mock_post):
        mock_post.return_value = FakeResponse(400, json_body=None, text="<html>not json</html>")
        with self.assertRaises(ProviderRequestError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_500_maps_to_provider_unavailable(self, mock_post):
        mock_post.return_value = FakeResponse(500, text="internal error")
        with self.assertRaises(ProviderUnavailableError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_502_maps_to_provider_unavailable(self, mock_post):
        mock_post.return_value = FakeResponse(502, text="bad gateway")
        with self.assertRaises(ProviderUnavailableError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_503_maps_to_provider_unavailable(self, mock_post):
        mock_post.return_value = FakeResponse(503, text="unavailable")
        with self.assertRaises(ProviderUnavailableError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_unexpected_4xx_is_non_retryable_and_distinct(self, mock_post):
        mock_post.return_value = FakeResponse(418, text="I'm a teapot")
        with self.assertRaises(UnexpectedProviderResponseError) as ctx:
            self._send()
        self.assertFalse(ctx.exception.retryable)

    # ── Network failures ─────────────────────────────────────────────────

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_timeout_maps_to_network_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("timed out")
        with self.assertRaises(NetworkTimeoutError):
            self._send()

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_connection_error_maps_to_network_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("unreachable")
        with self.assertRaises(NetworkTimeoutError):
            self._send()

    # ── Retryable flags on the mapped exceptions ────────────────────────

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_401_is_not_retryable(self, mock_post):
        mock_post.return_value = FakeResponse(401)
        try:
            self._send()
            self.fail("expected AuthenticationFailedError")
        except AuthenticationFailedError as exc:
            self.assertFalse(exc.retryable)

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_429_is_retryable(self, mock_post):
        mock_post.return_value = FakeResponse(429)
        try:
            self._send()
            self.fail("expected RateLimitError")
        except RateLimitError as exc:
            self.assertTrue(exc.retryable)

    @patch("accounts.email.providers.brevo_provider.requests.post")
    def test_500_is_retryable(self, mock_post):
        mock_post.return_value = FakeResponse(500)
        try:
            self._send()
            self.fail("expected ProviderUnavailableError")
        except ProviderUnavailableError as exc:
            self.assertTrue(exc.retryable)
