from unittest import TestCase
from unittest.mock import patch

from accounts.email.exceptions import InvalidEmailError, ProviderUnavailableError
from accounts.email.providers.base import EmailProvider, EmailSendResult
from accounts.email.service import EmailService
from accounts.tests.helpers import make_config


class FakeProvider(EmailProvider):
    def __init__(self, outcomes):
        # outcomes: list of results or exceptions to raise, consumed in order
        self._outcomes = list(outcomes)
        self.calls = []

    def send(self, to_email, subject, html_body, request_id):
        self.calls.append((to_email, subject))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class EmailServiceTests(TestCase):
    def test_send_registration_otp_dispatches_with_otp_template(self):
        provider = FakeProvider([EmailSendResult(provider_message_id="mid-1", provider_name="brevo")])
        service = EmailService(config=make_config(), provider=provider)

        service.send_registration_otp("user@example.com", "123456")

        self.assertEqual(len(provider.calls), 1)
        to_email, subject = provider.calls[0]
        self.assertEqual(to_email, "user@example.com")
        self.assertIn("Verification Code", subject)

    def test_send_reset_otp_dispatches_with_reset_template(self):
        provider = FakeProvider([EmailSendResult(provider_message_id="mid-2", provider_name="brevo")])
        service = EmailService(config=make_config(), provider=provider)

        service.send_reset_otp("user@example.com", "654321")

        to_email, subject = provider.calls[0]
        self.assertIn("Password Reset", subject)

    def test_invalid_recipient_raises_and_never_reaches_provider(self):
        provider = FakeProvider([])
        service = EmailService(config=make_config(), provider=provider)

        with self.assertRaises(InvalidEmailError):
            service.send_registration_otp("not-an-email", "123456")

        self.assertEqual(provider.calls, [])

    def test_provider_failure_propagates_as_email_service_error(self):
        provider = FakeProvider([ProviderUnavailableError("down")])
        service = EmailService(config=make_config(max_retries=1), provider=provider)

        with self.assertRaises(ProviderUnavailableError):
            service.send_welcome_email("user@example.com", "Name")

    def test_success_does_not_raise(self):
        provider = FakeProvider([EmailSendResult(provider_message_id="mid-3", provider_name="brevo")])
        service = EmailService(config=make_config(), provider=provider)
        # Should not raise.
        service.send_password_changed_email("user@example.com")
