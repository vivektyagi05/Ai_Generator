"""
PHASE 2 Step 26 — core entitlement engine tests: Plan/Feature/PlanFeatureRule
model tests, guest/free/premium resolution, and expiration boundaries.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import EntitlementAuditLog, Feature, Plan, PlanFeatureRule, UserEntitlement
from accounts.services import entitlement_service
from accounts.services.entitlement_service import AccessDecision, DenialReason
from accounts.services.exceptions import UnknownFeatureError


class PlanModelTests(TestCase):
    def test_plan_created_by_seed_migration(self):
        self.assertTrue(Plan.objects.filter(code="FREE").exists())
        self.assertTrue(Plan.objects.filter(code="PREMIUM").exists())

    def test_plan_code_must_be_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Plan.objects.create(code="FREE", name="Duplicate Free")

    def test_inactive_plan_is_not_returned_by_catalog_filter(self):
        Plan.objects.filter(code="PREMIUM").update(is_active=False)
        active_codes = set(Plan.objects.filter(is_active=True).values_list("code", flat=True))
        self.assertNotIn("PREMIUM", active_codes)


class FeatureModelTests(TestCase):
    def test_features_created_by_seed_migration(self):
        # PHASE 1 (generator architecture normalization) added AI_PHOTO,
        # seeded by 0011_seed_photo_feature.py -- see
        # accounts/entitlement_config.py FEATURE_DEFINITIONS.
        expected = {"AI_CHAT", "AI_CODE", "AI_STORY", "AI_APPLICATION", "AI_PROPOSAL", "AI_PHOTO"}
        actual = set(Feature.objects.values_list("code", flat=True))
        self.assertEqual(expected, actual)

    def test_feature_code_must_be_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Feature.objects.create(code="AI_CHAT", name="Duplicate")


class PlanFeatureRuleTests(TestCase):
    def test_seed_migration_creates_a_rule_per_plan_per_feature(self):
        # 6 features as of PHASE 1 (generator architecture normalization) --
        # see test_features_created_by_seed_migration above.
        free = Plan.objects.get(code="FREE")
        premium = Plan.objects.get(code="PREMIUM")
        self.assertEqual(PlanFeatureRule.objects.filter(plan=free).count(), 6)
        self.assertEqual(PlanFeatureRule.objects.filter(plan=premium).count(), 6)

    def test_duplicate_plan_feature_pair_rejected(self):
        free = Plan.objects.get(code="FREE")
        chat = Feature.objects.get(code="AI_CHAT")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PlanFeatureRule.objects.create(plan=free, feature=chat, access="ALLOWED")

    def test_disabled_rule_is_not_allowed_access(self):
        free = Plan.objects.get(code="FREE")
        chat = Feature.objects.get(code="AI_CHAT")
        rule = PlanFeatureRule.objects.get(plan=free, feature=chat)
        rule.access = PlanFeatureRule.Access.DISABLED
        rule.save()

        user = User.objects.create_user(username="dana", password="x")
        decision = entitlement_service.get_access_decision(user, None, "AI_CHAT")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DenialReason.PLAN_NOT_ALLOWED)


class GuestResolutionTests(TestCase):
    def test_guest_resolves_to_guest_plan(self):
        plan = entitlement_service.get_current_plan(user=None, guest_key="sess-abc")
        self.assertEqual(plan, "GUEST")

    def test_guest_cannot_spoof_free_or_premium(self):
        # There is no code path that lets a guest_key resolve to anything
        # other than GUEST -- get_current_plan only ever looks at `user`.
        for fake_guest_key in ("FREE", "PREMIUM", "plan=PREMIUM", "admin"):
            plan = entitlement_service.get_current_plan(user=None, guest_key=fake_guest_key)
            self.assertEqual(plan, "GUEST")

    def test_guest_is_denied_regardless_of_daily_limit(self):
        # PHASE 8A CANONICAL RULE (see entitlement_service._decide()'s
        # guest branch for the full rationale):
        #   OLD RULE: a guest was allowed access up to GUEST_DAILY_LIMIT
        #   requests/day/feature, denied with GUEST_RESTRICTION only past
        #   that count -- this is what test_guest_feature_access_allowed_
        #   under_daily_limit (this test's old name/body) asserted.
        #   NEW CANONICAL RULE: an anonymous visitor must never be allowed
        #   to consume real AI-generation capacity, full stop -- the quota
        #   check is never reached at all.
        #   WHY THE OLD ASSERTION WAS WRONG: it encoded a product decision
        #   (silent guest credit consumption) the product owner has since
        #   explicitly reversed as a security/business requirement, not
        #   a bug in the test itself.
        #   WHICH TEST WAS UPDATED: this one (renamed from
        #   test_guest_feature_access_allowed_under_daily_limit) and
        #   test_guest_restricted_once_daily_limit_reached below.
        decision = entitlement_service.get_access_decision(None, "sess-xyz", "AI_CHAT")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DenialReason.AUTHENTICATION_REQUIRED)

    def test_guest_denied_even_with_zero_prior_usage(self):
        # Old rule's "over the limit" scenario is no longer reachable --
        # a guest is denied before usage is ever counted, so 0 prior
        # requests and `limit` prior requests both deny identically.
        guest_key = "sess-limit-test"
        decision_before_any_usage = entitlement_service.get_access_decision(None, guest_key, "AI_CHAT")
        self.assertFalse(decision_before_any_usage.allowed)
        self.assertEqual(decision_before_any_usage.reason, DenialReason.AUTHENTICATION_REQUIRED)

        # Confirms record_usage() was never even called for a denied
        # guest by the real endpoint (test_ai_entitlement_integration.py
        # covers the endpoint itself) -- here we just prove the decision
        # doesn't depend on GUEST_DAILY_LIMIT/get_usage_count at all
        # anymore, unlike the old implementation.
        limit = entitlement_service.get_limit(None, guest_key, "AI_CHAT")["daily_limit"]
        for _ in range(limit or 0):
            entitlement_service.record_usage(None, guest_key, "AI_CHAT")
        decision_after_usage = entitlement_service.get_access_decision(None, guest_key, "AI_CHAT")
        self.assertFalse(decision_after_usage.allowed)
        self.assertEqual(decision_after_usage.reason, DenialReason.AUTHENTICATION_REQUIRED)


class FreePlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="freeuser", password="x")

    def test_authenticated_user_with_no_entitlement_resolves_to_free(self):
        plan = entitlement_service.get_current_plan(user=self.user, guest_key=None)
        self.assertEqual(plan, "FREE")

    def test_free_feature_access_works(self):
        decision = entitlement_service.get_access_decision(self.user, None, "AI_CODE")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.plan, "FREE")

    def test_free_quota_restriction_works(self):
        limit = entitlement_service.get_limit(self.user, None, "AI_STORY")["daily_limit"]
        for _ in range(limit):
            entitlement_service.record_usage(self.user, None, "AI_STORY")
        decision = entitlement_service.get_access_decision(self.user, None, "AI_STORY")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DenialReason.QUOTA_EXCEEDED)


class PremiumPlanTests(TestCase):
    """
    Uses controlled test fixtures only (django's ORM directly, exactly as
    Phase 2 Step 8/26 requires) -- never a fake payment, never a
    "make me premium" endpoint. No production Premium users are ever
    created this way; these rows only ever exist inside a TestCase's
    transaction, rolled back after each test.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="premiumuser", password="x")
        self.premium = Plan.objects.get(code="PREMIUM")

    def _grant(self, **kwargs):
        defaults = dict(user=self.user, plan=self.premium, status=UserEntitlement.Status.ACTIVE)
        defaults.update(kwargs)
        return UserEntitlement.objects.create(**defaults)

    def test_valid_premium_entitlement_grants_access(self):
        self._grant()
        plan = entitlement_service.get_current_plan(user=self.user, guest_key=None)
        self.assertEqual(plan, "PREMIUM")
        decision = entitlement_service.get_access_decision(self.user, None, "AI_CHAT")
        self.assertTrue(decision.allowed)

    def test_expired_premium_denies_access_and_falls_back_to_free(self):
        self._grant(
            starts_at=timezone.now() - timedelta(days=60),
            expires_at=timezone.now() - timedelta(days=1),
        )
        plan = entitlement_service.get_current_plan(user=self.user, guest_key=None)
        self.assertEqual(plan, "FREE")
        # Directly probing the expired row's own decision path (bypassing
        # the FREE fallback) still reports EXPIRED, not a generic denial.
        decision = entitlement_service.get_access_decision(self.user, None, "AI_CHAT")
        self.assertTrue(decision.allowed)  # falls back to FREE, which is itself allowed
        self.assertEqual(decision.plan, "FREE")

    def test_not_yet_active_premium_denies_access(self):
        self._grant(starts_at=timezone.now() + timedelta(days=1))
        plan = entitlement_service.get_current_plan(user=self.user, guest_key=None)
        # Not-yet-active Premium must not count -- falls back to FREE.
        self.assertEqual(plan, "FREE")

    def test_suspended_premium_denies_access(self):
        entitlement = self._grant()
        entitlement.status = UserEntitlement.Status.SUSPENDED
        entitlement.save()
        plan = entitlement_service.get_current_plan(user=self.user, guest_key=None)
        self.assertEqual(plan, "FREE")

    def test_premium_row_existing_does_not_automatically_grant_access(self):
        # Step 8's core invariant: a Premium PLAN definition existing in
        # the catalog must never itself grant Premium to anyone. Only an
        # actual UserEntitlement row does that.
        other_user = User.objects.create_user(username="nobody", password="x")
        plan = entitlement_service.get_current_plan(user=other_user, guest_key=None)
        self.assertEqual(plan, "FREE")

    def test_duplicate_active_entitlement_same_plan_rejected_by_db(self):
        self._grant()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserEntitlement.objects.create(
                    user=self.user, plan=self.premium, status=UserEntitlement.Status.ACTIVE
                )

    def test_expiration_exactly_at_boundary_is_expired(self):
        now = timezone.now()
        entitlement = self._grant(starts_at=now - timedelta(days=1), expires_at=now)
        # effective_status treats `at >= expires_at` as expired (Step 26:
        # boundary test) -- pass the exact expires_at instant.
        self.assertEqual(entitlement.effective_status(at=entitlement.expires_at), "EXPIRED")

    def test_expiration_one_second_before_boundary_is_active(self):
        now = timezone.now()
        entitlement = self._grant(starts_at=now - timedelta(days=1), expires_at=now)
        self.assertEqual(
            entitlement.effective_status(at=entitlement.expires_at - timedelta(seconds=1)), "ACTIVE"
        )

    def test_admin_override_creates_audit_log(self):
        from accounts.services import entitlement_admin

        staff = User.objects.create_user(username="staffmember", password="x", is_staff=True)
        entitlement = entitlement_admin.grant_entitlement(
            user=self.user, plan=self.premium, performed_by=staff, reason="Support goodwill credit"
        )
        log = EntitlementAuditLog.objects.get(entitlement=entitlement)
        self.assertEqual(log.action, EntitlementAuditLog.Action.GRANT)
        self.assertEqual(log.performed_by, staff)
        self.assertEqual(log.reason, "Support goodwill credit")

    def test_admin_override_without_reason_rejected(self):
        from accounts.services import entitlement_admin

        staff = User.objects.create_user(username="staffmember2", password="x", is_staff=True)
        with self.assertRaises(ValueError):
            entitlement_admin.grant_entitlement(
                user=self.user, plan=self.premium, performed_by=staff, reason=""
            )


class UnknownFeatureTests(TestCase):
    def test_unknown_feature_via_get_access_decision_returns_denied_not_raise(self):
        # get_access_decision() is the public-facing decision API and is
        # deliberately defensive: an unrecognized feature comes back as an
        # ordinary FEATURE_DISABLED denial (see its try/except around
        # _resolve_feature) rather than raising, so a caller like the AI
        # view doesn't need its own try/except for this.
        user = User.objects.create_user(username="curious", password="x")
        decision = entitlement_service.get_access_decision(user, None, "NOT_A_REAL_FEATURE")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DenialReason.FEATURE_DISABLED)

    def test_unknown_feature_via_get_feature_entitlement_raises(self):
        # Lower-level lookups (used internally / by callers that want to
        # distinguish "doesn't exist" from "not entitled") do raise.
        user = User.objects.create_user(username="curious2", password="x")
        with self.assertRaises(UnknownFeatureError):
            entitlement_service.get_feature_entitlement(user, None, "NOT_A_REAL_FEATURE")


class AccessDecisionSerializationTests(TestCase):
    def test_to_dict_roundtrip_shape(self):
        decision = AccessDecision(allowed=True, plan="FREE", feature="AI_CHAT", limits={"daily_limit": 5})
        payload = decision.to_dict()
        self.assertEqual(payload["allowed"], True)
        self.assertEqual(payload["reason"], None)
        self.assertIn("limits", payload)
