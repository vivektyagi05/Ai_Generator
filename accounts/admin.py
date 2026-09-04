from django.contrib import admin

from .models import (
    AIUsage,
    CreditAccount,
    CreditTransaction,
    EntitlementAuditLog,
    Feature,
    Payment,
    Plan,
    PlanFeatureRule,
    ProviderEvent,
    Subscription,
    SubscriptionAuditLog,
    UserEntitlement,
)
from .services import entitlement_admin, subscription_service


@admin.register(CreditAccount)
class CreditAccountAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "guest_session_key", "balance", "reserved_balance",
        "lifetime_earned", "lifetime_used", "updated_at",
    )
    search_fields = ("user__username", "user__email", "guest_session_key")
    readonly_fields = (
        "balance", "reserved_balance", "lifetime_earned", "lifetime_used",
        "created_at", "updated_at",
    )


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "credit_account", "transaction_type", "amount",
        "balance_after", "reserved_after", "reference_id", "created_at",
    )
    list_filter = ("transaction_type",)
    search_fields = ("reference_id", "credit_account__user__username")
    readonly_fields = [f.name for f in CreditTransaction._meta.fields]

    def has_delete_permission(self, request, obj=None):
        # Ledger rows are append-only -- see CreditTransaction's docstring.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = (
        "id", "request_id", "user", "guest_session_key", "feature", "status",
        "credits_reserved", "credits_charged", "credits_refunded", "created_at",
    )
    list_filter = ("status", "feature", "provider")
    search_fields = ("request_id", "user__username", "guest_session_key")


# ============================================================================
# PHASE 2 — Entitlement & Plan Engine admin (Step 19: admin-only, auditable,
# reason required, no arbitrary client endpoint, no silent changes)
# ============================================================================


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "is_public", "display_order")
    list_filter = ("is_active", "is_public")
    search_fields = ("code", "name")


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")


@admin.register(PlanFeatureRule)
class PlanFeatureRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id", "plan", "feature", "access", "daily_limit", "monthly_limit", "per_request_limit",
    )
    list_filter = ("plan", "access")
    search_fields = ("plan__code", "feature__code")


@admin.register(EntitlementAuditLog)
class EntitlementAuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "entitlement", "action", "performed_by", "reason", "created_at")
    list_filter = ("action",)
    search_fields = ("entitlement__user__username", "reason")
    readonly_fields = [f.name for f in EntitlementAuditLog._meta.fields]

    def has_delete_permission(self, request, obj=None):
        # Audit rows are append-only -- see EntitlementAuditLog's docstring.
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UserEntitlement)
class UserEntitlementAdmin(admin.ModelAdmin):
    """
    Deliberately does NOT let staff freely edit `status`/`expires_at`
    through the plain model form -- every real mutation goes through
    accounts/services/entitlement_admin.py (Step 19/26: no silent changes,
    every override audited) via the actions below. The default add/change
    form is still available for creating the *initial* grant (there is no
    other way to create the very first row), but every subsequent state
    change is an audited action, not a raw field edit.
    """

    list_display = ("id", "user", "plan", "status", "starts_at", "expires_at", "source")
    list_filter = ("plan", "status")
    search_fields = ("user__username", "user__email")
    actions = ["action_revoke", "action_suspend", "action_reactivate"]

    def save_model(self, request, obj, form, change):
        creating = obj.pk is None
        super().save_model(request, obj, form, change)
        if creating:
            from .models import EntitlementAuditLog as _Log

            _Log.objects.create(
                entitlement=obj,
                action=_Log.Action.GRANT,
                performed_by=request.user,
                reason="Created via Django admin form",
                metadata={"plan": obj.plan.code},
            )

    @admin.action(description="Revoke selected entitlements")
    def action_revoke(self, request, queryset):
        for entitlement in queryset:
            entitlement_admin.revoke_entitlement(
                entitlement=entitlement, performed_by=request.user, reason="Bulk admin action: revoke"
            )

    @admin.action(description="Suspend selected entitlements")
    def action_suspend(self, request, queryset):
        for entitlement in queryset:
            entitlement_admin.suspend_entitlement(
                entitlement=entitlement, performed_by=request.user, reason="Bulk admin action: suspend"
            )

    @admin.action(description="Reactivate selected entitlements")
    def action_reactivate(self, request, queryset):
        for entitlement in queryset:
            entitlement_admin.reactivate_entitlement(
                entitlement=entitlement, performed_by=request.user, reason="Bulk admin action: reactivate"
            )


# ============================================================================
# PHASE 3 — Subscription & Billing admin (Step 18/25: this is the ONLY
# place a subscription can be moved to ACTIVE without a real Phase 4
# payment webhook -- an admin-only, audited, staff-authenticated action,
# never a client-facing endpoint. Mirrors the Phase 2 UserEntitlement admin
# pattern above.)
# ============================================================================


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """
    Staff can create the initial PENDING row via the plain add form (there
    is no other way to create the very first row), but every subsequent
    state change is an audited action below, not a raw field edit -- status/
    current_period_start/current_period_end/cancel_at_period_end are
    read-only outside of that initial creation, same rationale as
    UserEntitlementAdmin above.
    """

    list_display = (
        "id", "user", "plan", "status", "billing_interval",
        "current_period_end", "cancel_at_period_end", "provider",
    )
    list_filter = ("status", "billing_interval", "plan", "provider")
    search_fields = ("user__username", "user__email", "provider_subscription_id")
    readonly_fields = (
        "current_period_start", "current_period_end", "ended_at",
        "entitlement", "last_provider_event_id", "created_at", "updated_at",
    )
    actions = ["action_activate", "action_renew", "action_cancel_now", "action_expire"]

    @admin.action(description="Activate selected subscriptions")
    def action_activate(self, request, queryset):
        for subscription in queryset:
            subscription_service.activate_subscription(
                subscription=subscription,
                performed_by=request.user,
                source="admin",
                reason="Bulk admin action: activate",
            )

    @admin.action(description="Renew selected subscriptions")
    def action_renew(self, request, queryset):
        for subscription in queryset:
            subscription_service.renew_subscription(
                subscription=subscription,
                performed_by=request.user,
                source="admin",
                reason="Bulk admin action: renew",
            )

    @admin.action(description="Cancel selected subscriptions now")
    def action_cancel_now(self, request, queryset):
        for subscription in queryset:
            subscription_service.cancel_subscription(
                subscription=subscription,
                performed_by=request.user,
                source="admin",
                reason="Bulk admin action: cancel now",
            )

    @admin.action(description="Expire selected subscriptions")
    def action_expire(self, request, queryset):
        for subscription in queryset:
            subscription_service.expire_subscription(
                subscription=subscription,
                performed_by=request.user,
                source="admin",
                reason="Bulk admin action: expire",
            )


@admin.register(SubscriptionAuditLog)
class SubscriptionAuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "subscription", "event_type", "performed_by", "source", "created_at")
    list_filter = ("event_type",)
    search_fields = ("subscription__user__username", "reason")
    readonly_fields = [f.name for f in SubscriptionAuditLog._meta.fields]

    def has_delete_permission(self, request, obj=None):
        # Audit rows are append-only -- see SubscriptionAuditLog's docstring.
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ============================================================================
# PHASE 4 — Payment admin (Step 31: "Admins must not be able to casually
# change payment success, payment amount, provider IDs, subscription
# activation"). Both models below are read-only in the admin -- there is no
# add form, no change form, no delete. The ONLY way these rows are ever
# written is accounts/services/payment_service.py, driven by a verified
# Razorpay checkout callback or webhook -- never a staff click.
# ============================================================================


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "user", "plan", "billing_interval", "status", "amount", "currency",
        "order_id", "payment_id", "captured_at", "created_at",
    )
    list_filter = ("status", "plan", "billing_interval", "provider")
    search_fields = ("user__username", "user__email", "order_id", "payment_id")
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProviderEvent)
class ProviderEventAdmin(admin.ModelAdmin):
    list_display = (
        "id", "provider", "event_id", "event_type", "status", "received_at", "processed_at",
    )
    list_filter = ("status", "event_type", "provider")
    search_fields = ("event_id",)
    readonly_fields = [f.name for f in ProviderEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
