# accounts/services/credit_service.py
"""
The ONLY code in this project allowed to mutate CreditAccount.balance or
CreditAccount.reserved_balance. Views, templates, JS, and provider code
must go through this module — never touch the model fields directly.

Every mutating function here:
  1. Runs inside `transaction.atomic()`.
  2. Takes `select_for_update()` on the CreditAccount row before reading it,
     so two concurrent calls for the same account serialize instead of
     racing (see the module docstring in accounts/models.py and
     PHASE_1_CREDIT_ENGINE.md for the SQLite-vs-Postgres caveat on how
     strong that lock actually is).
  3. Writes exactly one CreditTransaction ledger row describing the change,
     including balance_before/after and reserved_before/after — this is
     what makes every mutation auditable and what the DB-level
     `uq_credittxn_account_reference_type` constraint uses to guarantee a
     given (account, reference_id, transaction_type) can never be applied
     twice, even under a concurrent retry.

None of these functions trust a caller-supplied balance or "credits to
grant myself" value — the only inputs that affect the ledger are the
`amount` arguments passed by *server-side* code (AI orchestration, admin
tooling), never anything read out of a request body as a balance.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import credit_config
from accounts.models import CreditAccount, CreditTransaction
from accounts.services.exceptions import (
    CreditServiceError,
    DuplicateRequestError,
    InsufficientCreditsError,
    InvalidAmountError,
)

TransactionType = CreditTransaction.TransactionType

# Transaction types grant() is allowed to write. USAGE/RESERVE/REFUND go
# through reserve()/finalize()/refund() instead, which additionally touch
# reserved_balance in ways grant() deliberately doesn't support.
_GRANT_ALLOWED_TYPES = {
    TransactionType.GRANT,
    TransactionType.BONUS,
    TransactionType.ADMIN_ADJUSTMENT,
    TransactionType.EXPIRATION,
    TransactionType.MIGRATION,
}


# ============================================================================
# Account lookup / creation
# ============================================================================

def get_account(user=None, guest_key=None) -> CreditAccount | None:
    """Read-only lookup. Returns None if no account exists yet."""
    if bool(user) == bool(guest_key):
        raise CreditServiceError("Exactly one of user or guest_key must be provided.")
    try:
        if user is not None:
            return CreditAccount.objects.get(user=user)
        return CreditAccount.objects.get(guest_session_key=guest_key)
    except CreditAccount.DoesNotExist:
        return None


def get_or_create_account(user=None, guest_key=None) -> CreditAccount:
    """
    Returns the account for this user/guest, creating it (with the
    infrastructure-only initial free grant, Step 10) on first use.

    `get_or_create` handles the create-race itself (catches the IntegrityError
    from the unique constraint on user/guest_session_key and re-fetches), so
    two simultaneous first-requests from the same brand-new guest session
    can't create two accounts.
    """
    if bool(user) == bool(guest_key):
        raise CreditServiceError("Exactly one of user or guest_key must be provided.")

    lookup = {"user": user} if user is not None else {"guest_session_key": guest_key}
    account, created = CreditAccount.objects.get_or_create(**lookup)

    if created and credit_config.INITIAL_FREE_CREDITS > 0:
        try:
            grant(
                account,
                amount=credit_config.INITIAL_FREE_CREDITS,
                transaction_type=TransactionType.GRANT,
                reference_id=f"initial-grant:{account.pk}",
                description="Initial free credit grant",
            )
        except DuplicateRequestError:
            # Another concurrent request already granted it — fine, that's
            # exactly the idempotency guarantee working as intended.
            pass
        account.refresh_from_db()

    return account


def migrate_guest_credits_to_user(guest_key: str | None, user) -> CreditAccount:
    """
    PHASE 8 ARCHITECTURE RECOVERY -- Priority 2 (guest -> user credit
    lifecycle). Call this from the signup/login view in place of
    get_or_create_account(user=user), passing the guest_key captured
    BEFORE django.contrib.auth.login() runs (login() rotates the session
    key via cycle_key(), so the guest identity must be read first -- see
    accounts/services/guest_identity.py).

    Business rule (the Step 10 initial free grant is "once per identity,
    ever", not "once per CreditAccount row"):
      - No guest_key, or a guest_key with no CreditAccount ever created
        for it (this session never actually made a guest AI request):
        nothing to migrate. Falls through to the ordinary
        get_or_create_account(user=user) path, unchanged -- a genuinely
        new identity still gets the normal initial free grant.
      - A guest_key WITH an existing CreditAccount: that identity already
        received its one-time initial grant as a guest, so the new user
        account does NOT get a second one. Instead, whatever the guest
        account currently has available (balance - reserved_balance) is
        moved into the user's account as a paired MIGRATION entry (a
        negative entry on the guest account, a positive entry on the
        user account, same reference_id). A guest balance of exactly 0
        moves 0 credits -- no phantom credits are ever created just
        because a guest session happened to exist.
      - An in-flight reservation on the guest account (a guest request
        that reserved credits but hasn't finalized/refunded yet) is left
        alone: only the *available* balance migrates, so the guest
        account keeps enough balance to cover its own reservation and
        that reservation's later finalize()/refund() still resolves
        correctly against it.
      - Idempotent: the migration ledger entries use
        reference_id=f"guest-migration:{guest_key}", covered by the same
        `uq_credittxn_account_reference_type` DB constraint every other
        credit_service function relies on. A retried signup/login (or a
        second login using the same already-migrated guest session) is a
        no-op, not a double credit -- caught here via DuplicateRequestError.
      - The guest CreditAccount row itself is never deleted (its
        CreditTransaction history is append-only/immutable, same as
        every other account) -- it's simply left at balance ==
        reserved_balance (zero available), orphaned once the session
        that referenced it is gone.

    Out of scope here (tracked as a follow-up, not silently assumed):
    this only migrates the *credit balance*. AIUsage/ChatHistory rows
    created during the guest session keep their original ownership
    (guest requests never had a `user` to begin with -- see
    accounts/services/ai_usage_service.py) and are not reassigned by
    this function.

    Returns the user's CreditAccount either way.
    """
    if not guest_key:
        return get_or_create_account(user=user)

    with transaction.atomic():
        guest_account = (
            CreditAccount.objects.select_for_update()
            .filter(guest_session_key=guest_key)
            .first()
        )

        if guest_account is None:
            return get_or_create_account(user=user)

        user_account, user_account_created = CreditAccount.objects.get_or_create(user=user)
        user_account = CreditAccount.objects.select_for_update().get(pk=user_account.pk)

        migrated_amount = guest_account.available_balance
        reference_id = f"guest-migration:{guest_key}"

        if migrated_amount > 0:
            try:
                grant(
                    guest_account,
                    amount=-migrated_amount,
                    transaction_type=TransactionType.MIGRATION,
                    reference_id=reference_id,
                    description=f"Migrated to user_id={user.pk} on signup/login",
                )
                grant(
                    user_account,
                    amount=migrated_amount,
                    transaction_type=TransactionType.MIGRATION,
                    reference_id=reference_id,
                    description="Migrated from guest session on signup/login",
                )
            except DuplicateRequestError:
                # Already migrated by an earlier attempt (retried
                # signup/login submit) -- no-op, not a second credit.
                pass
        # migrated_amount == 0: guest identity existed and already used
        # its one-time initial grant (fully spent, or never granted
        # because credit_config.INITIAL_FREE_CREDITS was 0) -- correctly
        # grants nothing further here, whether or not user_account is
        # brand new.

        return CreditAccount.objects.get(pk=user_account.pk)


def get_balance_snapshot(account: CreditAccount) -> dict:
    """Authoritative current balance, read fresh from the database."""
    account.refresh_from_db()
    return {
        "balance": account.balance,
        "reserved_balance": account.reserved_balance,
        "available_balance": account.available_balance,
        "lifetime_earned": account.lifetime_earned,
        "lifetime_used": account.lifetime_used,
    }


def can_afford(account: CreditAccount, amount: int) -> bool:
    """Best-effort, non-locking check — a fast UI hint only. The
    authoritative check is the locked read inside reserve()."""
    account.refresh_from_db(fields=["balance", "reserved_balance"])
    return account.available_balance >= amount


# ============================================================================
# Ledger-mutating operations
# ============================================================================

def grant(
    account: CreditAccount,
    amount: int,
    transaction_type: str = TransactionType.GRANT,
    reference_id: str = "",
    description: str = "",
    metadata: dict | None = None,
) -> CreditTransaction:
    """
    Increases (GRANT/BONUS/positive ADMIN_ADJUSTMENT) or decreases
    (EXPIRATION/negative ADMIN_ADJUSTMENT) `balance` directly — never
    touches `reserved_balance`. Not for usage accounting; see
    reserve()/finalize()/refund() for that.
    """
    metadata = metadata or {}

    if transaction_type not in _GRANT_ALLOWED_TYPES:
        raise CreditServiceError(f"grant() cannot be used for transaction_type={transaction_type!r}")
    if transaction_type in (TransactionType.GRANT, TransactionType.BONUS) and amount <= 0:
        raise InvalidAmountError(f"{transaction_type} amount must be positive.")
    if transaction_type == TransactionType.EXPIRATION and amount >= 0:
        raise InvalidAmountError("EXPIRATION amount must be negative.")
    if transaction_type == TransactionType.ADMIN_ADJUSTMENT and amount == 0:
        raise InvalidAmountError("ADMIN_ADJUSTMENT amount must be non-zero.")
    if transaction_type == TransactionType.MIGRATION and amount == 0:
        raise InvalidAmountError("MIGRATION amount must be non-zero.")

    try:
        with transaction.atomic():
            acc = CreditAccount.objects.select_for_update().get(pk=account.pk)
            balance_before, reserved_before = acc.balance, acc.reserved_balance
            new_balance = balance_before + amount

            if new_balance < reserved_before:
                raise CreditServiceError(
                    "Adjustment would take balance below currently reserved credits."
                )

            acc.balance = new_balance
            update_fields = ["balance", "updated_at"]
            if amount > 0:
                acc.lifetime_earned = acc.lifetime_earned + amount
                update_fields.append("lifetime_earned")
            acc.save(update_fields=update_fields)

            txn = CreditTransaction.objects.create(
                user=acc.user,
                credit_account=acc,
                transaction_type=transaction_type,
                amount=amount,
                balance_before=balance_before,
                balance_after=acc.balance,
                reserved_before=reserved_before,
                reserved_after=reserved_before,
                reference_id=reference_id,
                description=description,
                metadata=metadata,
            )
    except IntegrityError as exc:
        raise DuplicateRequestError(
            f"A {transaction_type} transaction for reference_id={reference_id!r} already exists."
        ) from exc

    return txn


def reserve(
    account: CreditAccount,
    amount: int,
    reference_id: str,
    description: str = "",
    metadata: dict | None = None,
) -> CreditTransaction:
    """
    Atomically holds `amount` credits against the account's available
    balance (balance - reserved_balance) without spending them yet.
    Raises InsufficientCreditsError if the account can't cover it.
    """
    if amount <= 0:
        raise InvalidAmountError("Reservation amount must be positive.")
    if not reference_id:
        raise InvalidAmountError("reserve() requires a non-empty reference_id (the request's idempotency key).")
    metadata = metadata or {}

    try:
        with transaction.atomic():
            acc = CreditAccount.objects.select_for_update().get(pk=account.pk)
            available = acc.available_balance

            if available < amount:
                raise InsufficientCreditsError(
                    f"Insufficient credits: available={available}, requested={amount}"
                )

            balance_before, reserved_before = acc.balance, acc.reserved_balance
            acc.reserved_balance = reserved_before + amount
            acc.save(update_fields=["reserved_balance", "updated_at"])

            txn = CreditTransaction.objects.create(
                user=acc.user,
                credit_account=acc,
                transaction_type=TransactionType.RESERVE,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_before,
                reserved_before=reserved_before,
                reserved_after=acc.reserved_balance,
                reference_id=reference_id,
                description=description,
                metadata=metadata,
            )
    except IntegrityError as exc:
        raise DuplicateRequestError(
            f"A RESERVE transaction for reference_id={reference_id!r} already exists."
        ) from exc

    return txn


def finalize(
    account: CreditAccount,
    reservation_amount: int,
    actual_amount: int,
    reference_id: str,
    description: str = "",
    metadata: dict | None = None,
) -> tuple[CreditTransaction, int]:
    """
    Converts a reservation into a real charge. Releases the FULL
    reservation and deducts only `min(actual_amount, reservation_amount)`
    from balance — a request is never charged more than it reserved, even
    if the real usage came back higher than the pre-call estimate (that
    gap is a documented known limitation: the estimate must be
    conservative enough that this is rare, not something the account
    holder is exposed to).

    Returns (transaction, credits_actually_charged).
    """
    if reservation_amount <= 0:
        raise InvalidAmountError("reservation_amount must be positive.")
    if actual_amount < 0:
        raise InvalidAmountError("actual_amount cannot be negative.")
    if not reference_id:
        raise InvalidAmountError("finalize() requires a non-empty reference_id.")
    metadata = metadata or {}

    charge = min(actual_amount, reservation_amount)
    if actual_amount > reservation_amount:
        metadata = {**metadata, "actual_exceeded_reservation": True, "actual_amount": actual_amount}

    try:
        with transaction.atomic():
            acc = CreditAccount.objects.select_for_update().get(pk=account.pk)
            balance_before, reserved_before = acc.balance, acc.reserved_balance

            acc.balance = balance_before - charge
            acc.reserved_balance = reserved_before - reservation_amount
            acc.lifetime_used = acc.lifetime_used + charge
            acc.save(update_fields=["balance", "reserved_balance", "lifetime_used", "updated_at"])

            txn = CreditTransaction.objects.create(
                user=acc.user,
                credit_account=acc,
                transaction_type=TransactionType.USAGE,
                amount=-charge,
                balance_before=balance_before,
                balance_after=acc.balance,
                reserved_before=reserved_before,
                reserved_after=acc.reserved_balance,
                reference_id=reference_id,
                description=description,
                metadata=metadata,
            )
    except IntegrityError as exc:
        raise DuplicateRequestError(
            f"A USAGE transaction for reference_id={reference_id!r} already exists."
        ) from exc

    return txn, charge


def refund(
    account: CreditAccount,
    reservation_amount: int,
    reference_id: str,
    description: str = "",
    metadata: dict | None = None,
) -> CreditTransaction:
    """
    Releases a reservation entirely without charging anything — used when
    validation fails after reserving, the provider call fails, times out,
    or returns something unusable.
    """
    if reservation_amount <= 0:
        raise InvalidAmountError("reservation_amount must be positive.")
    if not reference_id:
        raise InvalidAmountError("refund() requires a non-empty reference_id.")
    metadata = metadata or {}

    try:
        with transaction.atomic():
            acc = CreditAccount.objects.select_for_update().get(pk=account.pk)
            balance_before, reserved_before = acc.balance, acc.reserved_balance

            acc.reserved_balance = reserved_before - reservation_amount
            acc.save(update_fields=["reserved_balance", "updated_at"])

            txn = CreditTransaction.objects.create(
                user=acc.user,
                credit_account=acc,
                transaction_type=TransactionType.REFUND,
                amount=reservation_amount,
                balance_before=balance_before,
                balance_after=balance_before,
                reserved_before=reserved_before,
                reserved_after=acc.reserved_balance,
                reference_id=reference_id,
                description=description,
                metadata=metadata,
            )
    except IntegrityError as exc:
        raise DuplicateRequestError(
            f"A REFUND transaction for reference_id={reference_id!r} already exists."
        ) from exc

    return txn


def grant_monthly_credits(account: CreditAccount) -> CreditTransaction | None:
    """
    Grants credit_config.MONTHLY_FREE_CREDITS at most once per
    (account, calendar month). This function does the granting; it is NOT
    wired to any scheduler (no Celery/cron dependency exists in this
    project — see Step 10 / known limitations). Call it from a management
    command run by an external scheduler, or on-demand.

    Returns the GRANT transaction, or None if this account already
    received its grant for the current period.
    """
    period = timezone.now().strftime("%Y-%m")

    with transaction.atomic():
        acc = CreditAccount.objects.select_for_update().get(pk=account.pk)
        if acc.last_monthly_grant_period == period:
            return None

        txn = grant(
            acc,
            amount=credit_config.MONTHLY_FREE_CREDITS,
            transaction_type=TransactionType.GRANT,
            reference_id=f"monthly-grant:{acc.pk}:{period}",
            description=f"Monthly free credit grant for {period}",
        )

        acc.last_monthly_grant_period = period
        acc.save(update_fields=["last_monthly_grant_period"])

    return txn
