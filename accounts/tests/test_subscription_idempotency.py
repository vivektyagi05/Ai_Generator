"""PHASE 3 Step 26 — idempotency tests: duplicate activation/renewal/
cancellation and duplicate provider event ids must never double-apply."""

from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import Plan, Subscription, SubscriptionAuditLog
from accounts.services import subscription_service


class SubscriptionIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dave", password="x")
        self.plan = Plan.objects.get(code="PREMIUM")
        self.sub = subscription_service.create_subscription(
            user=self.user, plan=self.plan, billing_interval=Subscription.BillingInterval.MONTHLY
        )

    def test_duplicate_activation_is_a_no_op(self):
        s1 = subscription_service.activate_subscription(subscription=self.sub)
        s2 = subscription_service.activate_subscription(subscription=s1)
        self.assertEqual(s1.current_period_end, s2.current_period_end)

    def test_duplicate_provider_event_id_does_not_reapply_activation(self):
        subscription_service.activate_subscription(
            subscription=self.sub, provider_event_id="evt_123"
        )
        self.sub.refresh_from_db()
        first_end = self.sub.current_period_end
        subscription_service.activate_subscription(
            subscription=self.sub, provider_event_id="evt_123"
        )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.current_period_end, first_end)

    def test_duplicate_renewal_provider_event_id_does_not_double_extend(self):
        sub = subscription_service.activate_subscription(subscription=self.sub)
        sub = subscription_service.renew_subscription(subscription=sub, provider_event_id="evt_renew_1")
        first_end = sub.current_period_end
        sub = subscription_service.renew_subscription(subscription=sub, provider_event_id="evt_renew_1")
        self.assertEqual(sub.current_period_end, first_end)

    def test_duplicate_cancellation_is_a_no_op(self):
        sub = subscription_service.activate_subscription(subscription=self.sub)
        sub = subscription_service.cancel_subscription(subscription=sub)
        ended_at_1 = sub.ended_at
        sub = subscription_service.cancel_subscription(subscription=sub)
        self.assertEqual(sub.ended_at, ended_at_1)

    def test_duplicate_activation_does_not_create_duplicate_audit_rows(self):
        subscription_service.activate_subscription(subscription=self.sub, provider_event_id="evt_x")
        subscription_service.activate_subscription(subscription=self.sub, provider_event_id="evt_x")
        activated_count = SubscriptionAuditLog.objects.filter(
            subscription=self.sub, event_type=SubscriptionAuditLog.EventType.ACTIVATED
        ).count()
        self.assertEqual(activated_count, 1)
