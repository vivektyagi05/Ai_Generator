from django.db import models
from django.utils import timezone
from datetime import timedelta

class ChatHistory(models.Model):
    # PHASE 5 STEP 4 — feature/type correction.
    #
    # Before this field existed, history_view() guessed a display "type"
    # by sniffing `response` (startswith("http") -> image; contains "<"
    # and ">" -> code; else text) -- see PHASE_5_AI_GENERATOR_FORENSIC_
    # FINDINGS.md sec.4 for the exact bug this caused (e.g. a Story/
    # Proposal result containing angle brackets was misclassified as
    # code). `feature` is the same slug already sent to /api/ai/ and
    # already known by accounts.ai_request_config.ALLOWED_FEATURES
    # ("chat","code","story","application","proposal","photo") -- it is
    # stored verbatim, not re-derived, so history_view() no longer needs
    # to guess for any row saved after this migration.
    #
    # blank/default="" (not a FEATURE_UNKNOWN sentinel) deliberately, so
    # existing rows created before this field existed decode as "no
    # reliable feature on record" rather than a guessed value -- Step 4
    # explicitly forbids inventing a classification where the evidence
    # (the stored response text) is not reliable evidence of it. Those
    # legacy rows still render via the same content-sniffing heuristic as
    # before, but are now explicitly flagged `is_legacy_classification`
    # in history_view() context instead of being presented as certain.
    FEATURE_CHOICES = (
        ("chat", "Chat"),
        ("code", "Code"),
        ("story", "Story"),
        ("application", "Application"),
        ("proposal", "Proposal"),
        ("photo", "Photo"),
    )

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    query = models.TextField()
    response = models.TextField()
    feature = models.CharField(max_length=20, choices=FEATURE_CHOICES, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # PHASE 8 STEP 2/3 -- Backend Data Foundation + History 2.0 continuity.
    #
    # Before this field, the only thing saved per generation was `query`
    # (accounts.views.save_history), and for the multi-field generators
    # (application, proposal, story, code) `query` is a LOSSY, human-
    # readable label built client-side in main.html -- e.g.
    # "Application: " + purpose, or "Proposal: " + title -- NOT the full
    # set of form inputs (name/recipient/reason/details for application;
    # audience/objective/benefits/budget/details for proposal; etc). That
    # data was never captured anywhere, so it cannot be reconstructed for
    # rows saved before this field existed -- see history_view()/
    # AI_GENERATORS.views.create() below, which restore only what a given
    # row genuinely has on record instead of guessing the rest from the
    # summary label.
    #
    # default=dict (not null) so `.inputs` is always a safe dict to read;
    # blank=True since legacy rows (and any future save path that has no
    # structured inputs to offer) correctly have nothing here.
    inputs = models.JSONField(default=dict, blank=True)

from django.db import models
from django.utils import timezone
from datetime import timedelta

class EmailOTP(models.Model):
    PURPOSE_CHOICES = (
        ("reset", "Password Reset"),
        ("signup", "Signup"),
    )

    email = models.EmailField()
    otp = models.CharField(max_length=6)
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES)

    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["email", "purpose"],
                name="accounts_emailotp_one_active_per_email_purpose",
            ),
        ]

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=5)

    def __str__(self):
        return f"{self.email} - {self.purpose}"


class PendingSignup(models.Model):
    """
    Server-side holding record for a signup that hasn't completed OTP
    verification yet.

    Replaces storing the raw password in request.session["signup_data"]:
    the password is hashed with Django's password hasher *once*, here, at
    signup time, and only that hash — never the plaintext — is ever
    persisted or referenced from the session. The session only carries the
    email as a pointer to this record.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=15)

    def __str__(self):
        return f"pending signup: {self.email}"



from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True)

    # PHASE 9 — Step 7: self-service account "deletion" no longer hard-
    # deletes the User row (see accounts/services/account_deletion_service.py
    # for why: it CASCADE-deleted real payment/credit/usage history along
    # with it). This is the marker for "deletion" having happened --
    # non-null means the account is anonymized/deactivated. Living on
    # UserProfile (not auth.User, which this project doesn't customize)
    # because it's an app-owned model we can freely migrate.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return self.user.username


# ============================================================================
# PHASE 1 — REAL AI CREDIT ENGINE
# ----------------------------------------------------------------------------
# Server-authoritative credit accounting. The frontend is NEVER trusted for
# balance, deduction, refunds, usage, or limits — every mutation below goes
# through accounts/services/credit_service.py, which wraps changes in a DB
# transaction (+ select_for_update where the backend supports it) and always
# writes a matching CreditTransaction ledger row. Nothing here mutates
# `balance` / `reserved_balance` directly outside that service layer.
#
# One CreditAccount exists per *authoritative identity*, which is either an
# authenticated Django User OR a server-issued guest session key (see
# accounts/services/guest_identity.py) — never both, never a synthetic User
# row created just to hold credits for a guest (see CK_ACCOUNT_OWNER below).
# ============================================================================

from django.db.models import F, Q


class CreditAccount(models.Model):
    """
    The single authoritative source of truth for a user's (or guest's)
    credit balance. Exactly one row per identity.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="credit_account",
        null=True,
        blank=True,
    )
    # Server-issued Django session key for guest (unauthenticated) callers.
    # This is NEVER a client-supplied value — see guest_identity.py, which
    # relies on Django's signed session cookie (django.contrib.sessions),
    # not on anything the client can set or forge.
    guest_session_key = models.CharField(
        max_length=64, null=True, blank=True, unique=True, db_index=True
    )

    balance = models.PositiveBigIntegerField(default=0)
    reserved_balance = models.PositiveBigIntegerField(default=0)
    lifetime_earned = models.PositiveBigIntegerField(default=0)
    lifetime_used = models.PositiveBigIntegerField(default=0)

    # Bookkeeping for the (infrastructure-only, not scheduled) monthly grant
    # — see accounts/services/credit_service.py:grant_monthly_credits().
    last_monthly_grant_period = models.CharField(max_length=7, blank=True)  # "YYYY-MM"

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Every account belongs to exactly one identity: a User XOR a guest session.
            models.CheckConstraint(
                check=(
                    Q(user__isnull=False, guest_session_key__isnull=True)
                    | Q(user__isnull=True, guest_session_key__isnull=False)
                ),
                name="ck_creditaccount_exactly_one_owner",
            ),
            models.CheckConstraint(check=Q(balance__gte=0), name="ck_creditaccount_balance_gte_0"),
            models.CheckConstraint(
                check=Q(reserved_balance__gte=0), name="ck_creditaccount_reserved_gte_0"
            ),
            # available_balance = balance - reserved_balance must never go negative.
            models.CheckConstraint(
                check=Q(balance__gte=F("reserved_balance")),
                name="ck_creditaccount_balance_gte_reserved",
            ),
        ]
        indexes = [
            models.Index(fields=["guest_session_key"]),
        ]

    @property
    def available_balance(self) -> int:
        return self.balance - self.reserved_balance

    def __str__(self):
        owner = f"user={self.user_id}" if self.user_id else f"guest={self.guest_session_key}"
        return f"CreditAccount({owner}, balance={self.balance}, reserved={self.reserved_balance})"


class CreditTransaction(models.Model):
    """
    Append-only accounting ledger. Every balance / reserved_balance mutation
    on a CreditAccount has exactly one corresponding row here. Rows are never
    edited or deleted by application code — history is immutable.
    """

    class TransactionType(models.TextChoices):
        GRANT = "GRANT", "Grant"
        BONUS = "BONUS", "Bonus"
        RESERVE = "RESERVE", "Reserve"          # reserved_balance increases; balance unchanged
        USAGE = "USAGE", "Usage"                # reservation finalized into a real charge
        REFUND = "REFUND", "Refund"             # reservation released without a charge
        EXPIRATION = "EXPIRATION", "Expiration"
        ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT", "Admin Adjustment"
        # PHASE 8 ARCHITECTURE RECOVERY -- Priority 2. Written in pairs (a
        # negative entry on the guest CreditAccount, a positive entry on
        # the destination user CreditAccount, same reference_id) by
        # credit_service.migrate_guest_credits_to_user() when a guest
        # signs up or logs in. Kept distinct from ADMIN_ADJUSTMENT/GRANT
        # so the ledger reads honestly -- this is neither an admin action
        # nor a fresh grant, it's the same credits moving accounts.
        MIGRATION = "MIGRATION", "Migration"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="credit_transactions", null=True, blank=True
    )
    credit_account = models.ForeignKey(
        CreditAccount, on_delete=models.CASCADE, related_name="transactions"
    )
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)

    # Signed magnitude of this entry's effect, kept for quick auditing —
    # the authoritative record is still balance_before/after (+ reserved).
    amount = models.IntegerField()

    balance_before = models.PositiveBigIntegerField()
    balance_after = models.PositiveBigIntegerField()
    reserved_before = models.PositiveBigIntegerField()
    reserved_after = models.PositiveBigIntegerField()

    # Ties this ledger entry back to the AIUsage.request_id (or an
    # admin/grant reference) that caused it. Combined with transaction_type
    # into a uniqueness constraint below so the SAME request can never
    # reserve/finalize/refund twice at the database level, even if a bug in
    # application logic tried to.
    reference_id = models.CharField(max_length=255, blank=True, db_index=True)
    description = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(balance_before__gte=0), name="ck_credittxn_bal_before_gte_0"),
            models.CheckConstraint(check=Q(balance_after__gte=0), name="ck_credittxn_bal_after_gte_0"),
            models.CheckConstraint(
                check=Q(reserved_before__gte=0), name="ck_credittxn_res_before_gte_0"
            ),
            models.CheckConstraint(check=Q(reserved_after__gte=0), name="ck_credittxn_res_after_gte_0"),
            # DB-level idempotency backstop: for the request-scoped types, the
            # same (account, reference_id, type) can only ever be written once.
            models.UniqueConstraint(
                fields=["credit_account", "reference_id", "transaction_type"],
                condition=Q(reference_id__gt=""),
                name="uq_credittxn_account_reference_type",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["credit_account", "-created_at"]),
            models.Index(fields=["reference_id"]),
            models.Index(fields=["transaction_type"]),
        ]

    def __str__(self):
        return f"{self.transaction_type} {self.amount} (account={self.credit_account_id}, ref={self.reference_id!r})"


class AIUsage(models.Model):
    """
    One row per AI request that reached (or attempted to reach) the
    provider. This is the operational record; CreditTransaction is the
    financial ledger. request_id ties the two together and is the
    idempotency key for the whole request lifecycle.
    """

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        VALIDATION_FAILURE = "VALIDATION_FAILURE", "Validation Failure"
        INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS", "Insufficient Credits"
        RESERVED = "RESERVED", "Reserved"
        PROVIDER_SUCCESS = "PROVIDER_SUCCESS", "Provider Success"
        PROVIDER_FAILURE = "PROVIDER_FAILURE", "Provider Failure"
        TIMEOUT = "TIMEOUT", "Timeout"
        MALFORMED_RESPONSE = "MALFORMED_RESPONSE", "Malformed Response"
        FINALIZED = "FINALIZED", "Finalized"
        REFUNDED = "REFUNDED", "Refunded"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="ai_usages", null=True, blank=True
    )
    guest_session_key = models.CharField(max_length=64, blank=True, db_index=True)

    credit_account = models.ForeignKey(
        CreditAccount, on_delete=models.CASCADE, related_name="ai_usages", null=True, blank=True
    )

    # Idempotency key. Supplied by the caller when available, generated
    # server-side otherwise (see known limitations in PHASE_1_CREDIT_ENGINE.md
    # re: naive client retries when the caller doesn't supply one).
    request_id = models.CharField(max_length=64, unique=True)

    feature = models.CharField(max_length=50, default="chat")
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)

    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    usage_unavailable = models.BooleanField(default=False)

    credits_reserved = models.PositiveIntegerField(default=0)
    credits_charged = models.PositiveIntegerField(default=0)
    credits_refunded = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.REQUESTED)
    error_code = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)

    # Small, non-sensitive replay data (e.g. truncated result text) so an
    # idempotent replay of a FINALIZED request can return the same response
    # without re-calling the provider or re-charging. Never store prompts/
    # completions verbatim here beyond what's needed for replay — this is a
    # documented tradeoff, see PHASE_1_CREDIT_ENGINE.md.
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(user__isnull=False, guest_session_key="")
                    | (Q(user__isnull=True) & ~Q(guest_session_key=""))
                ),
                name="ck_aiusage_user_or_guest",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["guest_session_key", "-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self):
        return f"AIUsage({self.request_id}, status={self.status})"


# ============================================================================
# PHASE 2 — ENTITLEMENT & PLAN ENGINE
# ----------------------------------------------------------------------------
# Server-authoritative "who can access what" domain, deliberately separate
# from CreditAccount ("can this request be paid for") and rate_limit.py
# ("has this identity made too many requests too quickly"). See
# accounts/services/entitlement_service.py for the single service that reads
# these models — nothing else in the project is allowed to duplicate this
# decision. See PHASE_2_ENTITLEMENT_ENGINE.md for the full design writeup.
# ============================================================================


class Plan(models.Model):
    """
    A product access plan (catalog definition only — this row existing does
    NOT mean any user holds it; see UserEntitlement for that). GUEST is a
    virtual plan resolved in code for unauthenticated identities (Step 6 of
    the Phase 2 spec) and intentionally has no catalog row here — nothing
    about the guest funnel depends on a database record existing.
    """

    class Code(models.TextChoices):
        FREE = "FREE", "Free"
        PREMIUM = "PREMIUM", "Premium"

    code = models.CharField(max_length=30, choices=Code.choices, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    # Whether this plan appears on the public catalog / pricing page
    # (Step 13/14) — kept separate from is_active so a plan can be disabled
    # everywhere (is_active=False) independent of whether it was ever meant
    # to be publicly listed (e.g. an internal/legacy plan).
    is_public = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "code"]

    def __str__(self):
        return self.code


class Feature(models.Model):
    """
    Central registry of application capabilities. Codes map 1:1 onto the
    existing AIUsage.feature slugs (accounts/ai_request_config.py
    ALLOWED_FEATURES) — see FEATURE_SLUG_MAP in accounts/entitlement_config.py.
    Nothing here describes a capability that doesn't actually exist in the
    codebase (Step 2: "do not expose nonexistent functionality").
    """

    class Category(models.TextChoices):
        AI_GENERATOR = "AI_GENERATOR", "AI Generator"

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=30, choices=Category.choices, default=Category.AI_GENERATOR
    )
    # A feature registered but not yet backed by real code — Step 2 allows
    # registering future codes ONLY if clearly marked unavailable. No such
    # rows are seeded in Phase 2 (every seeded Feature is real and live),
    # this flag exists so that future registration doesn't require a schema
    # change.
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class PlanFeatureRule(models.Model):
    """
    What a given Plan provides for a given Feature. A (plan, feature) pair
    with no row here means "not entitled" — the entitlement service treats
    absence as denial, never as an implicit allow (Step 22: fail closed).
    """

    class Access(models.TextChoices):
        ALLOWED = "ALLOWED", "Allowed"
        DISABLED = "DISABLED", "Disabled"

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="feature_rules")
    feature = models.ForeignKey(Feature, on_delete=models.CASCADE, related_name="plan_rules")
    access = models.CharField(max_length=20, choices=Access.choices, default=Access.ALLOWED)

    # Independent limit dimensions (Step 16/17) — any may be null, meaning
    # "no limit of this kind is enforced for this plan/feature". These are
    # request-count quotas, NOT credit amounts (Step 10: credit vs
    # entitlement vs quota are different decisions, never conflated).
    daily_limit = models.PositiveIntegerField(null=True, blank=True)
    monthly_limit = models.PositiveIntegerField(null=True, blank=True)
    per_request_limit = models.PositiveIntegerField(null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "feature"], name="uq_planfeaturerule_plan_feature"
            ),
        ]
        indexes = [
            models.Index(fields=["plan", "feature"]),
        ]

    def __str__(self):
        return f"{self.plan_id}:{self.feature_id}={self.access}"


class UserEntitlement(models.Model):
    """
    The concrete grant of a Plan to an authenticated User for a time window.
    Guests never get a row here (Step 6: guest resolves to the virtual GUEST
    plan purely in code, from the existing server-issued session identity —
    never a DB grant, never a permanent record). FREE is likewise not
    normally stored here either: an authenticated user with no active
    UserEntitlement resolves to FREE by default (Step 7) — a row is only
    created when a plan needs an explicit, time-bounded grant, which today
    means Premium, created only through admin-audited overrides (Step 19) or
    test fixtures (Step 8), never a client-facing endpoint.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        SUSPENDED = "SUSPENDED", "Suspended"
        # EXPIRED is intentionally NOT a stored status — expiration is a
        # computed property (is_expired / effective_status below) derived
        # from expires_at vs now, so it can never drift out of sync with
        # the clock the way a stored, separately-updated status could.

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="entitlements"
    )
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="user_entitlements")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = no expiration

    # Free-text audit trail of where this grant came from (e.g.
    # "admin_override:<staff_username>", "test_fixture"). Never a payment
    # reference in Phase 2 — see PHASE_2_ENTITLEMENT_ENGINE.md "Future
    # payment compatibility" for how Phase 3/4 will populate this instead
    # via a Subscription FK once that model exists.
    source = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(expires_at__isnull=True) | Q(expires_at__gt=F("starts_at")),
                name="ck_userentitlement_expires_after_starts",
            ),
            # A user may hold at most one ACTIVE-status entitlement per plan
            # at a time (Step 20/26: no duplicate active entitlement for the
            # same identity/feature-granting plan). Multiple INACTIVE/
            # SUSPENDED historical rows are fine — history is kept, never
            # deleted or overwritten.
            models.UniqueConstraint(
                fields=["user", "plan"],
                condition=Q(status="ACTIVE"),
                name="uq_userentitlement_one_active_per_user_plan",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["plan", "status"]),
            models.Index(fields=["expires_at"]),
        ]

    def is_within_window(self, at=None) -> bool:
        at = at or timezone.now()
        if at < self.starts_at:
            return False
        if self.expires_at is not None and at >= self.expires_at:
            return False
        return True

    def effective_status(self, at=None) -> str:
        """
        Computed, timezone-aware status used by the entitlement service —
        never trust `status` alone without also checking the time window
        (Step 18: before start -> inactive, within window -> active, after
        expiry -> expired).
        """
        at = at or timezone.now()
        if self.status == self.Status.SUSPENDED:
            return "SUSPENDED"
        if self.status != self.Status.ACTIVE:
            return "INACTIVE"
        if at < self.starts_at:
            return "INACTIVE"
        if self.expires_at is not None and at >= self.expires_at:
            return "EXPIRED"
        return "ACTIVE"

    def __str__(self):
        return f"UserEntitlement(user={self.user_id}, plan={self.plan_id}, status={self.status})"


class EntitlementAuditLog(models.Model):
    """
    Append-only audit trail for every administrative entitlement change
    (Step 19: every manual override creates an audit record; no silent
    changes). Never edited or deleted by application code, mirroring
    CreditTransaction's immutability pattern.
    """

    class Action(models.TextChoices):
        GRANT = "GRANT", "Grant"
        REVOKE = "REVOKE", "Revoke"
        SUSPEND = "SUSPEND", "Suspend"
        REACTIVATE = "REACTIVATE", "Reactivate"
        EXTEND = "EXTEND", "Extend"

    entitlement = models.ForeignKey(
        UserEntitlement, on_delete=models.CASCADE, related_name="audit_logs"
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="entitlement_actions"
    )
    reason = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["entitlement", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} on entitlement={self.entitlement_id} by={self.performed_by_id}"


# ============================================================================
# PHASE 3 — SUBSCRIPTION & BILLING DOMAIN
# ----------------------------------------------------------------------------
# "Does this user have a commercial subscription, and under what billing
# terms?" -- deliberately separate from UserEntitlement ("what can this user
# access") and Plan ("what does a subscription to this plan provide"). See
# accounts/services/subscription_service.py for the single service allowed
# to mutate Subscription state, and PHASE_3_SUBSCRIPTION_ARCHITECTURE.md for
# the full design writeup.
#
# No payment provider is called from this module or from subscription_service
# -- Subscription.provider / provider_subscription_id are an identity
# abstraction only, ready for Phase 4 to populate, never dialled out to here.
# ============================================================================


class Subscription(models.Model):
    """
    A user's commercial subscription relationship with a Plan. Existence of
    a Subscription row does NOT by itself grant access -- only an ACTIVE
    Subscription drives a corresponding UserEntitlement grant (via
    subscription_service.activate_subscription), and even then the
    entitlement is the thing entitlement_service actually reads.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        PAST_DUE = "PAST_DUE", "Past Due"
        GRACE_PERIOD = "GRACE_PERIOD", "Grace Period"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    # Terminal states -- once here, a subscription never transitions again;
    # a plan change or resubscribe creates a new Subscription row instead
    # (Step 15: plan-change readiness without rewriting this state machine).
    TERMINAL_STATUSES = (Status.CANCELLED, Status.EXPIRED)

    # Non-terminal ("live") states -- see uq_subscription_one_live_per_user
    # below. A user may have at most one live Subscription at a time; a
    # future upgrade/downgrade flow (Step 15) settles the old one to a
    # terminal state before/while creating the new one, rather than this
    # model supporting two simultaneously-live subscriptions per user.
    LIVE_STATUSES = (Status.PENDING, Status.ACTIVE, Status.PAST_DUE, Status.GRACE_PERIOD)

    class BillingInterval(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly"
        YEARLY = "YEARLY", "Yearly"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    billing_interval = models.CharField(max_length=10, choices=BillingInterval.choices)

    starts_at = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)

    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # The UserEntitlement this subscription drives. Set the first time the
    # subscription becomes ACTIVE and reused (extended) on every renewal --
    # never a second entitlement row per subscription (Step 7/8: Subscription
    # -> UserEntitlement -> Plan, one entitlement per live subscription).
    entitlement = models.OneToOneField(
        UserEntitlement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription",
    )

    # Payment-provider identity abstraction only (Step 17/21) -- populated
    # by Phase 4, never called into from Phase 3. Blank/empty in every
    # Phase-3-created row.
    provider = models.CharField(max_length=50, blank=True)
    provider_subscription_id = models.CharField(max_length=255, blank=True, null=True, unique=True)

    # Idempotency backstop for provider event replay (Step 10) -- the last
    # provider event id that was successfully applied to this subscription.
    # A future webhook handler compares an incoming event id against this
    # before calling renew/cancel/etc again.
    last_provider_event_id = models.CharField(max_length=255, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # At most one live (non-terminal) subscription per user at a
            # time (Step 14/23) -- enforced at the DB level, not only in
            # Python, so two concurrent activation requests can't both
            # succeed (Step 23: concurrency).
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status__in=["PENDING", "ACTIVE", "PAST_DUE", "GRACE_PERIOD"]),
                name="uq_subscription_one_live_per_user",
            ),
            models.CheckConstraint(
                check=Q(current_period_end__isnull=True)
                | Q(current_period_start__isnull=True)
                | Q(current_period_end__gt=F("current_period_start")),
                name="ck_subscription_period_end_after_start",
            ),
            models.CheckConstraint(
                check=Q(
                    status__in=[
                        "PENDING",
                        "ACTIVE",
                        "PAST_DUE",
                        "GRACE_PERIOD",
                        "CANCELLED",
                        "EXPIRED",
                    ]
                ),
                name="ck_subscription_valid_status",
            ),
            models.CheckConstraint(
                check=Q(billing_interval__in=["MONTHLY", "YEARLY"]),
                name="ck_subscription_valid_billing_interval",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "current_period_end"]),
            models.Index(fields=["provider", "provider_subscription_id"]),
        ]

    def is_period_expired(self, at=None) -> bool:
        at = at or timezone.now()
        return self.current_period_end is not None and at >= self.current_period_end

    def __str__(self):
        return f"Subscription(user={self.user_id}, plan={self.plan_id}, status={self.status})"


class SubscriptionAuditLog(models.Model):
    """
    Append-only audit trail for every meaningful Subscription state change
    (Step 16), mirroring EntitlementAuditLog/CreditTransaction's immutable-
    ledger pattern. Never stores payment secrets -- only identifiers and
    business-relevant metadata (Step 16: "do not store sensitive payment
    secrets").
    """

    class EventType(models.TextChoices):
        CREATED = "CREATED", "Created"
        ACTIVATED = "ACTIVATED", "Activated"
        RENEWED = "RENEWED", "Renewed"
        CANCELLATION_SCHEDULED = "CANCELLATION_SCHEDULED", "Cancellation Scheduled"
        CANCELLED = "CANCELLED", "Cancelled"
        PAYMENT_FAILED = "PAYMENT_FAILED", "Payment Failed"
        GRACE_STARTED = "GRACE_STARTED", "Grace Started"
        EXPIRED = "EXPIRED", "Expired"
        RESTORED = "RESTORED", "Restored"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="audit_logs"
    )
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    # The staff/system actor responsible, when it's a real authenticated
    # user (e.g. an admin override). Null for server-driven transitions
    # (renewal, expiration) -- those are described by `source` instead.
    performed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_actions"
    )
    # Free-text origin of a server-driven change, e.g. "system:renewal",
    # "system:expiration_sync", "subscription_service". Complements
    # performed_by rather than duplicating it.
    source = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["subscription", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} on subscription={self.subscription_id}"


# ============================================================================
# PHASE 4 — REAL PAYMENT PROCESSING (RAZORPAY)
# ----------------------------------------------------------------------------
# "Did a real payment happen, for how much, and does it correspond to a
# genuine Razorpay-side event" -- deliberately separate from Subscription
# (Step 4: "Do NOT use Subscription as the payment ledger"). Payment is an
# append-mostly transaction record; ProviderEvent is the append-only webhook
# delivery log. See accounts/services/payment_service.py for the single
# service allowed to mutate these models, and PHASE_4_PAYMENT_ARCHITECTURE.md
# for the full design writeup.
#
# The browser is never the source of truth for any field here -- every
# column is written from server-resolved data (accounts/billing_config.py)
# or from a cryptographically verified Razorpay payload (checkout signature
# or webhook signature), never copied from an unauthenticated client body.
# ============================================================================


class Payment(models.Model):
    """
    One row per payment attempt (Razorpay Order). Money is always stored as
    an integer count of the currency's minor unit (paise for INR) -- never
    as a float rupee amount, which cannot exactly represent every valid
    price and would let rounding drift the ledger over time (Step 6).
    """

    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        AUTHORIZED = "AUTHORIZED", "Authorized"
        CAPTURED = "CAPTURED", "Captured"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    # Terminal states -- once here, a Payment row never transitions again.
    # A retried checkout after FAILED creates a new Payment row via a fresh
    # create-order call, mirroring Subscription's TERMINAL_STATUSES pattern.
    TERMINAL_STATUSES = (Status.FAILED, Status.REFUNDED)

    # A user may have at most one CREATED (in-flight, not yet resolved)
    # Payment at a time -- see uq_payment_one_created_per_user below. This
    # is the idempotency backstop for double-click / browser retry on the
    # create-order endpoint (Step 9): a second request while one is already
    # in flight reuses the existing row instead of minting a second Razorpay
    # order.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")

    # The Subscription this payment is paying for -- the PENDING subscription
    # being activated for a first-time purchase, or the already-ACTIVE
    # subscription being renewed. Never null once a Payment leaves CREATED;
    # required at creation time (Step 17: "payment belongs to expected
    # user/subscription context").
    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="payments", null=True, blank=True
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments")
    billing_interval = models.CharField(max_length=10, choices=Subscription.BillingInterval.choices)

    # Payment-provider identity. "razorpay" today; kept as a free string
    # (not a hardcoded choice) so a second provider could be added later
    # without a migration touching this column, same rationale as
    # Subscription.provider in Phase 3.
    provider = models.CharField(max_length=50, default="razorpay")

    order_id = models.CharField(max_length=255, blank=True, null=True)
    payment_id = models.CharField(max_length=255, blank=True, null=True)
    # The last Razorpay signature this row was verified against. Not a
    # secret (it's a proof artifact, not a key) -- kept for audit only,
    # never re-derived from or trusted without re-running verify_signature.
    signature = models.CharField(max_length=255, blank=True)

    # Server-resolved at creation time from accounts/billing_config.py --
    # never accepted from the client (Step 7/8).
    amount = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=10, default="INR")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)
    method = models.CharField(max_length=30, blank=True)  # e.g. "card", "upi" -- from provider payload
    captured_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=500, blank=True)

    # Idempotency backstop for provider event replay (Step 15), mirroring
    # Subscription.last_provider_event_id -- the last webhook event id that
    # was successfully applied to this row.
    provider_event_id = models.CharField(max_length=255, blank=True)

    # Non-sensitive operational context only -- e.g. {"contact": "...",
    # "notes": {...}} echoed back by Razorpay. NEVER the Razorpay secret,
    # NEVER the webhook secret, NEVER raw webhook payloads with unrelated
    # customer data (Step 30: "never log ... full sensitive payloads
    # unnecessarily" applies equally to what's persisted here).
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "order_id"],
                condition=Q(order_id__isnull=False),
                name="uq_payment_provider_order_id",
            ),
            models.UniqueConstraint(
                fields=["provider", "payment_id"],
                condition=Q(payment_id__isnull=False),
                name="uq_payment_provider_payment_id",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="CREATED"),
                name="uq_payment_one_created_per_user",
            ),
            models.CheckConstraint(check=Q(amount__gt=0), name="ck_payment_amount_gt_0"),
            models.CheckConstraint(
                check=Q(
                    status__in=["CREATED", "AUTHORIZED", "CAPTURED", "FAILED", "REFUNDED"]
                ),
                name="ck_payment_valid_status",
            ),
            models.CheckConstraint(
                check=Q(billing_interval__in=["MONTHLY", "YEARLY"]),
                name="ck_payment_valid_billing_interval",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["provider", "order_id"]),
            models.Index(fields=["provider", "payment_id"]),
            models.Index(fields=["subscription"]),
        ]

    def __str__(self):
        return f"Payment(user={self.user_id}, order={self.order_id}, status={self.status})"


class ProviderEvent(models.Model):
    """
    Append-only log of every Razorpay webhook delivery this app has
    received, keyed by Razorpay's own event id (Step 14/15). Existence of a
    PROCESSED row for a given (provider, event_id) is what makes webhook
    replay safe -- Razorpay is explicitly permitted to (and does, in
    practice) deliver the same event more than once.
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        IGNORED = "IGNORED", "Ignored"  # signature valid, but event type not handled
        FAILED = "FAILED", "Failed"  # signature valid, handling raised an error

    provider = models.CharField(max_length=50, default="razorpay")
    # Razorpay's `x-razorpay-event-id` delivery header -- unique per
    # delivery attempt (redeliveries of the same logical event reuse the
    # same id), which is exactly the property this table's uniqueness
    # constraint depends on.
    event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=100)  # e.g. "payment.captured"

    # SHA-256 of the raw request body, stored so a webhook replayed with a
    # mutated payload under the same event_id (which should never happen
    # from the real provider, but is exactly the kind of thing a forged
    # replay would try) is detectable without persisting the full payload.
    payload_hash = models.CharField(max_length=64)
    # Small, non-sensitive subset of the payload useful for support/debug
    # (e.g. order_id, payment_id, status) -- never the full raw body, never
    # card/UPI/bank details (Step 14: "never store unnecessary secrets").
    payload_summary = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    processing_error = models.CharField(max_length=500, blank=True)

    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "event_id"], name="uq_providerevent_provider_event_id"
            ),
        ]
        indexes = [
            models.Index(fields=["provider", "event_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["-received_at"]),
        ]

    def __str__(self):
        return f"ProviderEvent({self.provider}:{self.event_id}, {self.event_type}, {self.status})"