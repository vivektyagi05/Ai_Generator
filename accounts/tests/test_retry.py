from unittest import TestCase
from unittest.mock import patch

from accounts.email.exceptions import (
    AuthenticationFailedError,
    InvalidEmailError,
    NetworkTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from accounts.email.retry import send_with_retry


class RetryTests(TestCase):
    def setUp(self):
        # Don't actually sleep during tests.
        patcher = patch("accounts.email.retry.time.sleep", return_value=None)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_success_on_first_attempt_does_not_retry(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            return "ok"

        result = send_with_retry(send_fn, max_retries=3, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 1)

    def test_no_retry_for_invalid_email_400(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            raise InvalidEmailError("bad email")

        with self.assertRaises(InvalidEmailError):
            send_with_retry(send_fn, max_retries=3, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(calls["n"], 1)

    def test_no_retry_for_auth_failure_401(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            raise AuthenticationFailedError("bad key")

        with self.assertRaises(AuthenticationFailedError):
            send_with_retry(send_fn, max_retries=3, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(calls["n"], 1)

    def test_retries_on_429_up_to_max(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            raise RateLimitError("rate limited")

        with self.assertRaises(RateLimitError):
            send_with_retry(send_fn, max_retries=3, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(calls["n"], 3)

    def test_retries_on_5xx(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            raise ProviderUnavailableError("5xx")

        with self.assertRaises(ProviderUnavailableError):
            send_with_retry(send_fn, max_retries=2, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(calls["n"], 2)

    def test_retries_on_timeout(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            raise NetworkTimeoutError("timeout")

        with self.assertRaises(NetworkTimeoutError):
            send_with_retry(send_fn, max_retries=2, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(calls["n"], 2)

    def test_succeeds_after_transient_failure(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise ProviderUnavailableError("5xx")
            return "ok"

        result = send_with_retry(send_fn, max_retries=3, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 2)

    def test_exponential_backoff_delays(self):
        delays = []

        def fake_sleep(seconds):
            delays.append(seconds)

        with patch("accounts.email.retry.time.sleep", side_effect=fake_sleep):
            def send_fn():
                raise ProviderUnavailableError("5xx")

            with self.assertRaises(ProviderUnavailableError):
                send_with_retry(send_fn, max_retries=4, base_delay_seconds=1, request_id="r1")

        # attempts 1,2,3 fail-and-retry (delay before attempt 2,3,4);
        # attempt 4 fails and exhausts retries with no further sleep.
        self.assertEqual(delays, [1, 2, 4])

    def test_never_retries_more_than_max_retries_times(self):
        calls = {"n": 0}

        def send_fn():
            calls["n"] += 1
            raise ProviderUnavailableError("5xx")

        with self.assertRaises(ProviderUnavailableError):
            send_with_retry(send_fn, max_retries=5, base_delay_seconds=0.01, request_id="r1")
        self.assertEqual(calls["n"], 5)
