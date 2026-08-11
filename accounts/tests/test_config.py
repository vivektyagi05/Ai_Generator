import os
from unittest import TestCase
from unittest.mock import patch

from accounts.email.config import load_email_config
from accounts.email.exceptions import ConfigurationError


class LoadEmailConfigTests(TestCase):
    def _env(self, **overrides):
        base = {
            "EMAIL_PROVIDER": "brevo",
            "BREVO_API_KEY": "key-123",
            "EMAIL_FROM": "noreply@example.com",
            "EMAIL_FROM_NAME": "Example",
        }
        base.update(overrides)
        return base

    def test_loads_valid_config(self):
        with patch.dict(os.environ, self._env(), clear=False):
            config = load_email_config()
        self.assertEqual(config.provider, "brevo")
        self.assertEqual(config.api_key, "key-123")
        self.assertEqual(config.from_email, "noreply@example.com")

    def test_missing_api_key_raises_configuration_error(self):
        env = self._env(BREVO_API_KEY="")
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigurationError):
                load_email_config()

    def test_missing_from_email_raises_configuration_error(self):
        env = self._env(EMAIL_FROM="")
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigurationError):
                load_email_config()

    def test_unsupported_provider_raises_configuration_error(self):
        env = self._env(EMAIL_PROVIDER="sendgrid")
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigurationError):
                load_email_config()

    def test_invalid_timeout_raises(self):
        env = self._env(EMAIL_TIMEOUT_SECONDS="not-a-number")
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ValueError):
                load_email_config()

    def test_defaults_applied_when_optional_vars_absent(self):
        env = self._env()
        with patch.dict(os.environ, env, clear=False):
            for key in ("EMAIL_TIMEOUT_SECONDS", "EMAIL_MAX_RETRIES", "EMAIL_RETRY_BASE_DELAY"):
                os.environ.pop(key, None)
            config = load_email_config()
        self.assertEqual(config.request_timeout_seconds, 10.0)
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.retry_base_delay_seconds, 1.0)
