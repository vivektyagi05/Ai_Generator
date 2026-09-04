# accounts/billing_utils.py
"""
PHASE 3 — Centralized billing-period math.

The one place that knows how to turn a `period_start` + `billing_interval`
into a `period_end`. Nothing in views/services/admin is allowed to
hand-roll this (Step 4: "do not hardcode interval behavior inside views").

Correctness requirements this module exists to satisfy:
  - Months are calendar months, never approximated as "30 days" (a Jan 31
    monthly subscription must land on Feb 28/29, not Mar 2).
  - Year addition respects leap years (Feb 29 start -> Feb 28 on a
    non-leap target year).
  - Everything stays timezone-aware; naive datetimes are rejected outright
    rather than silently treated as UTC/local (Step 4: "timezone-aware
    datetimes").
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from django.utils import timezone


class NaiveDatetimeError(ValueError):
    """Raised when a naive (non-timezone-aware) datetime is passed in."""


def _require_aware(dt: datetime) -> None:
    if timezone.is_naive(dt):
        raise NaiveDatetimeError(
            f"billing_utils requires timezone-aware datetimes, got naive: {dt!r}"
        )


def add_calendar_months(dt: datetime, months: int) -> datetime:
    """
    Adds `months` calendar months to `dt`, clamping the day-of-month to the
    last valid day of the target month when the source day doesn't exist
    there (e.g. Jan 31 + 1 month -> Feb 28 or Feb 29, never Mar 2/3).
    """
    _require_aware(dt)
    total_month_index = dt.month - 1 + months
    target_year = dt.year + total_month_index // 12
    target_month = total_month_index % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(dt.day, last_day)
    return dt.replace(year=target_year, month=target_month, day=target_day)


def add_calendar_years(dt: datetime, years: int) -> datetime:
    """
    Adds `years` calendar years to `dt`, clamping Feb 29 -> Feb 28 when the
    target year isn't a leap year.
    """
    _require_aware(dt)
    return add_calendar_months(dt, years * 12)


# Mirrors the Subscription.BillingInterval choices, kept as plain strings
# here so this module has no model import / no circular-import risk.
MONTHLY = "MONTHLY"
YEARLY = "YEARLY"


def compute_period_end(period_start: datetime, interval: str) -> datetime:
    """
    The single authoritative "given a period start and a billing interval,
    when does this period end" calculation. Used for both the very first
    period (Subscription creation/activation) and every subsequent renewal
    -- so a renewal always advances from the *previous period's end*, never
    from "now", to avoid drift (Step 9: "advance current_period_start/end"
    from the correct anchor rather than accumulating rounding error).
    """
    _require_aware(period_start)
    if interval == MONTHLY:
        return add_calendar_months(period_start, 1)
    if interval == YEARLY:
        return add_calendar_years(period_start, 1)
    raise ValueError(f"Unknown billing interval: {interval!r}")


def grace_period_end(entered_at: datetime, grace_days: int) -> datetime:
    """Deterministic grace-period deadline: `entered_at` + `grace_days`
    (a plain day count is appropriate here, unlike month/year billing
    periods, since grace windows are configured in days -- see
    entitlement_config-style GRACE_PERIOD_DAYS)."""
    _require_aware(entered_at)
    return entered_at + timedelta(days=grace_days)
