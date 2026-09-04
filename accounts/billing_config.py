# accounts/billing_config.py
"""
PHASE 4 — Server-side billing price catalog.

This is the ONLY authoritative source for "how much does plan X cost for
billing interval Y". The client never supplies a price or amount for a
checkout -- accounts/services/payment_service.py always resolves the
payable amount by calling resolve_price() below, never by reading anything
out of the request body (Step 7/17: "The server must resolve ... requested
plan -> server-side Plan configuration -> server-side payable amount").

Design note: accounts/models.py's Plan (Phase 2) is a catalog/entitlement
row -- it does not carry a price, and Phase 2/3 were deliberately built
without one (see Subscription's docstring: "no payment provider is ever
called from here"). Rather than bolt a price field onto Plan (which would
mix "what does this plan unlock" with "what does it cost", two different
concerns with different audiences -- entitlement_service reads the former,
payment_service reads the latter) this module follows the same convention
already used for accounts/credit_config.py and accounts/entitlement_config.py:
business figures live in one place, in code, overridable from Django
settings/the environment, never hardcoded into a view or template.

All amounts are integer minor units (paise for INR) -- see Payment.amount's
docstring in models.py for why float rupees are never used as the
canonical representation.
"""

from __future__ import annotations

from django.conf import settings

# Only plans in this dict are payable at all. FREE is deliberately absent --
# there is no such thing as "paying for FREE", and payment_service.py
# rejects any plan_code not present here before it ever resolves an amount
# or talks to Razorpay.
#
# Every value is a documented PLACEHOLDER, same disclosed convention as
# PLAN_DEFINITIONS in entitlement_config.py -- product has not set real
# business pricing for this project. The *shape* (paise, per-interval,
# per-plan) is what's real and load-bearing.
CURRENCY = "INR"

_DEFAULT_PREMIUM_MONTHLY_PAISE = 29900  # ₹299.00
_DEFAULT_PREMIUM_YEARLY_PAISE = 299900  # ₹2,999.00

PLAN_PRICING_PAISE: dict[str, dict[str, int]] = {
    "PREMIUM": {
        "MONTHLY": int(
            getattr(settings, "BILLING_PREMIUM_MONTHLY_PAISE", _DEFAULT_PREMIUM_MONTHLY_PAISE)
        ),
        "YEARLY": int(
            getattr(settings, "BILLING_PREMIUM_YEARLY_PAISE", _DEFAULT_PREMIUM_YEARLY_PAISE)
        ),
    },
}


class UnpayablePlanError(ValueError):
    """Raised when a plan_code/billing_interval combination has no price
    configured -- i.e. it is not something a client is allowed to buy."""


def resolve_price(plan_code: str, billing_interval: str) -> tuple[int, str]:
    """
    The single authoritative "what does this cost" lookup. Returns
    (amount_in_paise, currency). Never accepts an amount as input -- there
    is nothing for a caller to override.
    """
    plan_prices = PLAN_PRICING_PAISE.get(plan_code)
    if not plan_prices or billing_interval not in plan_prices:
        raise UnpayablePlanError(
            f"No price configured for plan_code={plan_code!r} "
            f"billing_interval={billing_interval!r}."
        )
    amount = plan_prices[billing_interval]
    if amount <= 0:
        raise UnpayablePlanError(
            f"Configured price for plan_code={plan_code!r} "
            f"billing_interval={billing_interval!r} is not positive."
        )
    return amount, CURRENCY


def is_payable_plan(plan_code: str) -> bool:
    return plan_code in PLAN_PRICING_PAISE
