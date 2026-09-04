# accounts/services/razorpay_client.py
"""
PHASE 4 — Thin, fail-safe wrapper around the official `razorpay` Python
SDK (https://github.com/razorpay/razorpay-python, pinned in
requirements.txt).

This is the ONLY module in the project that imports `razorpay` or reads
settings.RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET/RAZORPAY_WEBHOOK_SECRET.
Everything else (payment_service, billing_views, webhook_views) calls the
functions below -- never the SDK directly, never `os.environ` directly.
This keeps "where do payment secrets get read from" auditable at a single
choke point.

Fails safely: if credentials are missing (e.g. local dev, CI), every
function here raises RazorpayNotConfiguredError rather than constructing a
client with an empty/None secret. Callers (billing_views/webhook_views)
turn that into a 503 with no payment attempted -- never a silent fake
success (top-level "STRICT REAL-MONEY RULE": "Fail safely if production
payment configuration is missing").

Never logs RAZORPAY_KEY_SECRET or RAZORPAY_WEBHOOK_SECRET. Never returns
them to a caller for inclusion in an HTTP response (see
create_order()'s docstring for what IS safe to return to the frontend).
"""

from __future__ import annotations

import logging

import razorpay
from django.conf import settings

logger = logging.getLogger(__name__)


class RazorpayNotConfiguredError(RuntimeError):
    """Raised when RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are not set. Callers
    must turn this into a safe error response, never a fake success."""


class SignatureVerificationFailed(ValueError):
    """Raised when a checkout or webhook signature does not verify against
    the configured secret. Callers must treat this as a security event:
    log it (without secrets), do not activate anything, do not leak
    whether the mismatch was in order_id/payment_id/signature format."""


def _require_credentials() -> tuple[str, str]:
    key_id = settings.RAZORPAY_KEY_ID
    key_secret = settings.RAZORPAY_KEY_SECRET
    if not key_id or not key_secret:
        raise RazorpayNotConfiguredError(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not configured."
        )
    return key_id, key_secret


def get_client() -> razorpay.Client:
    """Constructs a fresh SDK client from settings. Never caches the
    secret on a module-level object that could be introspected/dumped
    elsewhere; the cost of re-constructing per call is negligible."""
    key_id, key_secret = _require_credentials()
    return razorpay.Client(auth=(key_id, key_secret))


def get_public_key_id() -> str:
    """The ONLY Razorpay value that is ever safe to send to the browser
    (Step 10: 'NEVER: key_secret, webhook_secret, internal secrets').
    Raises RazorpayNotConfiguredError rather than returning an empty
    string, so a misconfigured deployment fails loudly server-side instead
    of shipping a broken checkout to the browser."""
    key_id, _ = _require_credentials()
    return key_id


def create_order(*, amount: int, currency: str, receipt: str, notes: dict | None = None) -> dict:
    """
    Creates a Razorpay Order. `amount` MUST already be server-resolved
    integer minor units (paise) -- see accounts/billing_config.py -- never
    a value taken from the request. `payment_capture=1` is fixed here (not
    caller-configurable) so successful checkouts are auto-captured by
    Razorpay rather than left AUTHORIZED-only, which would need a separate
    manual capture step this project does not implement (see
    PHASE_4_PAYMENT_ARCHITECTURE.md "Payment success vs capture").
    """
    if amount <= 0:
        raise ValueError("amount must be a positive integer (paise).")
    client = get_client()
    try:
        return client.order.create(
            {
                "amount": amount,
                "currency": currency,
                "receipt": receipt,
                "payment_capture": 1,
                "notes": notes or {},
            }
        )
    except razorpay.errors.BadRequestError:
        logger.warning("Razorpay order creation rejected", extra={"receipt": receipt})
        raise


def verify_checkout_signature(*, order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verifies the `razorpay_signature` a browser reports back after
    checkout, per Razorpay's documented HMAC-SHA256 scheme (order_id +
    "|" + payment_id, keyed with RAZORPAY_KEY_SECRET) — Step 11: "Do not
    simply compare strings without cryptographic verification."

    Returns True on success. Raises SignatureVerificationFailed (never a
    bare False that a caller might accidentally ignore) on any mismatch --
    including a malformed/missing field, which is folded into the same
    verification-failed outcome rather than a different code path that
    might be handled less strictly.
    """
    client = get_client()
    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )
        return True
    except razorpay.errors.SignatureVerificationError as exc:
        logger.warning(
            "Razorpay checkout signature verification failed",
            extra={"order_id": order_id, "payment_id": payment_id},
        )
        raise SignatureVerificationFailed(str(exc)) from exc
    except (KeyError, TypeError) as exc:
        # Malformed input (missing/wrong-typed field) -- same fail-closed
        # outcome as a cryptographic mismatch, never treated as "maybe ok".
        logger.warning("Razorpay checkout signature payload malformed")
        raise SignatureVerificationFailed("malformed signature payload") from exc


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    """
    Verifies the `X-Razorpay-Signature` header on an incoming webhook POST
    against RAZORPAY_WEBHOOK_SECRET (a DIFFERENT secret from
    RAZORPAY_KEY_SECRET -- Step 13). `raw_body` must be the exact,
    unmodified request bytes Django received -- re-serializing parsed JSON
    before verifying would change the byte sequence HMAC is computed over
    and could make a valid signature appear invalid (or, worse, could be
    exploited if re-serialization silently normalizes attacker-controlled
    formatting differences). See webhook_views.py for how this is called.
    """
    key_id = settings.RAZORPAY_KEY_ID
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not key_id or not webhook_secret:
        raise RazorpayNotConfiguredError(
            "RAZORPAY_WEBHOOK_SECRET is not configured."
        )
    # verify_webhook_signature() only needs the secret, not the API
    # key/secret pair, but the SDK's Utility is attached to a Client --
    # construct one with a syntactically valid (never network-called)
    # auth tuple purely to reach the utility helper.
    client = razorpay.Client(auth=(key_id, "unused"))
    try:
        client.utility.verify_webhook_signature(
            raw_body.decode("utf-8"), signature, webhook_secret
        )
        return True
    except razorpay.errors.SignatureVerificationError as exc:
        logger.warning("Razorpay webhook signature verification failed")
        raise SignatureVerificationFailed(str(exc)) from exc
    except UnicodeDecodeError as exc:
        logger.warning("Razorpay webhook body was not valid UTF-8")
        raise SignatureVerificationFailed("malformed webhook body") from exc


def fetch_payment(payment_id: str) -> dict:
    """
    Fetches a payment's current state directly from Razorpay's API, used
    as a defense-in-depth cross-check after checkout-signature verification
    and before trusting client-reported amount/currency/status (Step 12:
    'Do not blindly trust payment metadata supplied by frontend'). Never
    used as a *replacement* for webhook-driven activation -- see
    PHASE_4_PAYMENT_ARCHITECTURE.md.
    """
    client = get_client()
    return client.payment.fetch(payment_id)
