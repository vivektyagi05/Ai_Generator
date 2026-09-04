# PHASE 8A AUTH / OTP AUDIT

Full trace is in PHASE_8A_PROGRESS.md Item 4 — this is the audit
summary.

## Already correct before this phase (verified, not assumed)

- No OTP issued for a nonexistent account:
  `accounts/views.py:forgot_send_otp()` checks `User.objects.filter(
  email=email).exists()` and returns before calling `issue_otp()` if
  not.
- Identical generic response for registered vs. unregistered email at
  the send step, including on cooldown-throttled and
  email-provider-failure paths.
- No dummy/blank OTP acceptance: `otp_service.verify_otp()` requires a
  real, persisted `EmailOTP` row and `hmac.compare_digest`.
- Expiry, single-use (row deleted on success), max-attempts lockout
  (row deleted), send cooldown + rolling window, per-IP throttle: all
  present, all separately tested.
- CSRF: no `@csrf_exempt` anywhere in `accounts/views.py`.
- Session binding: `reset_email`/`otp_verified` are server-side session
  keys only; `forgot_reset_password` fails closed if the session claims
  verification but the user row is gone; session is flushed after a
  successful reset; a replayed submit after flush 403s cleanly.
- Session fixation on login: `user_login()` reads guest identity
  *before* calling Django's `login()`, which itself rotates the
  session key.
- Open redirect on login's `next` param: validated via
  `url_has_allowed_host_and_scheme()`.

## Real gap found this phase, and fixed

`otp_service.verify_otp()`'s `EmailOTP.DoesNotExist` branch returned a
message ("Verification code not found or already used...")
distinguishable from the wrong-code-on-a-real-OTP branch ("Invalid
verification code."). Since no `EmailOTP` row is ever created for an
unregistered email, this let an attacker: send-otp for a candidate
email (generic response either way) → immediately verify-otp with any
guess → read which message came back to learn whether the account
exists — bypassing the send-step's enumeration resistance via a
different endpoint entirely.

**Fix:** the `DoesNotExist` branch now returns the identical
message/status as the wrong-code branch. `expired`/`max-attempts`
messages deliberately left distinct (lower severity, requires a record
to have genuinely existed, legitimate UX signal for a user who knows
they received a real code). Shared service function — the fix applies
identically to the signup OTP flow (`verify_otp_record` alias), one fix
not two.

**Test:** `test_forgot_password.py::test_verify_step_gives_identical_response_for_unregistered_email_and_wrong_code`.

## Files changed

`accounts/otp_service.py`, `accounts/tests/test_forgot_password.py`.
