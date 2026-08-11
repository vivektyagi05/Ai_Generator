"""
OTP Utilities — Shared, cryptographically-secure OTP generation.

Every OTP-issuing code path (signup, signup resend, forgot-password,
forgot-password resend) must go through `generate_otp()` here rather than
calling `random`/`secrets` directly, so there is exactly one place that
defines "what an OTP looks like" and exactly one place using a secure RNG.
"""

import secrets

OTP_LENGTH = 6
OTP_MAX_VALUE = 10 ** OTP_LENGTH  # 1_000_000


def generate_otp() -> str:
    """
    Generate a cryptographically secure, zero-padded, fixed-length numeric OTP.

    Uses `secrets.randbelow`, not `random`, because `random` is a
    Mersenne-Twister PRNG that is predictable given enough output and must
    never be used for anything security-sensitive (session codes, tokens,
    OTPs). Never logged by any caller.
    """
    value = secrets.randbelow(OTP_MAX_VALUE)
    return str(value).zfill(OTP_LENGTH)
