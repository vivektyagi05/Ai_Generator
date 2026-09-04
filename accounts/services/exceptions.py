# accounts/services/exceptions.py
"""Typed exceptions raised by the credit engine's service layer."""


class CreditServiceError(Exception):
    """Base class for all credit-service errors."""


class InvalidAmountError(CreditServiceError):
    """Raised when a caller passes an amount that's invalid for the operation
    (zero/negative where a positive value is required, etc.)."""


class InsufficientCreditsError(CreditServiceError):
    """Raised by reserve() when the account's available balance
    (balance - reserved_balance) is less than the amount requested."""


class DuplicateRequestError(CreditServiceError):
    """
    Raised when a ledger-mutating operation (reserve/finalize/refund/grant)
    is attempted twice for the same (account, reference_id, transaction_type)
    — the database's uq_credittxn_account_reference_type constraint is the
    ultimate source of truth for this; this exception is the friendly
    translation of the resulting IntegrityError.
    """


# ============================================================================
# PHASE 2 — Entitlement service exceptions
# ============================================================================


class EntitlementServiceError(Exception):
    """Base class for all entitlement-service errors."""


class UnknownFeatureError(EntitlementServiceError):
    """Raised when a feature code/slug isn't registered in the Feature table."""


class InvalidIdentityError(EntitlementServiceError):
    """Raised when a caller passes neither (or both) of user/guest_key where
    exactly one is required."""


# ============================================================================
# PHASE 3 — Subscription service exceptions
# ============================================================================


class SubscriptionServiceError(Exception):
    """Base class for all subscription-service errors."""


class InvalidTransitionError(SubscriptionServiceError):
    """Raised when a caller requests a subscription status transition that
    isn't in the state machine's allowed-transitions table (see
    subscription_service.VALID_TRANSITIONS)."""


class DuplicateSubscriptionError(SubscriptionServiceError):
    """Raised when a user already has a live (non-terminal) subscription and
    another one is requested -- mirrors the DB-level uq_subscription_one_live_per_user
    constraint, translated into a friendly exception."""


class SubscriptionNotFoundError(SubscriptionServiceError):
    """Raised when an operation is requested against a subscription that
    doesn't exist for the resolved identity (never leaks whether some other
    user's subscription id exists -- see Step 25 IDOR requirements)."""


# ============================================================================
# PHASE 4 — Payment service exceptions
# ============================================================================


class PaymentServiceError(Exception):
    """Base class for all payment-service errors."""


class UnpayablePlanError(PaymentServiceError):
    """Raised when a client requests a checkout for a plan/interval that
    has no configured price in accounts/billing_config.py -- i.e. it is
    not something a client is allowed to buy."""


class DuplicateLiveSubscriptionError(PaymentServiceError):
    """Raised when a user requests a first-time checkout while they already
    hold a live (non-terminal) subscription that isn't the one being paid
    for -- mirrors subscription_service.DuplicateSubscriptionError."""


class PaymentVerificationError(PaymentServiceError):
    """Raised when a checkout callback's signature is valid but the
    payment's server-verified amount/currency/order/user context does not
    match what was expected (Step 17: amount/currency/order/user
    mismatch). Never activates anything; always recorded as a security/
    audit event."""


class PaymentNotFoundError(PaymentServiceError):
    """Raised when an operation references a Payment/order id that does
    not exist, or exists but does not belong to the resolved identity
    (never leaks which -- see Step 23 IDOR requirements)."""
