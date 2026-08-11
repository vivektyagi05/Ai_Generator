"""Shared test helpers: a fake `requests.Response` and a canned Brevo config."""

from accounts.email.config import EmailConfig


class FakeResponse:
    """Minimal stand-in for requests.Response, enough for BrevoProvider."""

    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text if text else (str(json_body) if json_body is not None else "")

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


def make_config(**overrides):
    defaults = dict(
        provider="brevo",
        api_key="test-api-key",
        from_email="noreply@example.com",
        from_name="Test Sender",
        request_timeout_seconds=5.0,
        max_retries=3,
        retry_base_delay_seconds=0,  # no real sleeping in tests
    )
    defaults.update(overrides)
    return EmailConfig(**defaults)
