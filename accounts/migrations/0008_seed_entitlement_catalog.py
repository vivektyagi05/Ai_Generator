# accounts/migrations/0008_seed_entitlement_catalog.py
"""
PHASE 2 — Data migration seeding the Plan/Feature/PlanFeatureRule catalog
from accounts/entitlement_config.py. Idempotent (update_or_create), and
reversible (the reverse_code removes exactly the rows this migration
creates, nothing else -- never touches UserEntitlement, which may hold
real grants by the time this is ever rolled back).
"""
from django.db import migrations

from accounts import entitlement_config


def seed_catalog(apps, schema_editor):
    Plan = apps.get_model("accounts", "Plan")
    Feature = apps.get_model("accounts", "Feature")
    PlanFeatureRule = apps.get_model("accounts", "PlanFeatureRule")

    feature_rows = {}
    for code, _slug, name in entitlement_config.FEATURE_DEFINITIONS:
        feature, _ = Feature.objects.update_or_create(
            code=code, defaults={"name": name, "is_active": True}
        )
        feature_rows[code] = feature

    for plan_def in entitlement_config.PLAN_DEFINITIONS:
        plan, _ = Plan.objects.update_or_create(
            code=plan_def["code"],
            defaults={
                "name": plan_def["name"],
                "description": plan_def["description"],
                "display_order": plan_def["display_order"],
                "is_public": plan_def["is_public"],
                "is_active": True,
            },
        )
        for rule_def in entitlement_config.plan_feature_rules_for(plan_def["code"]):
            PlanFeatureRule.objects.update_or_create(
                plan=plan,
                feature=feature_rows[rule_def["feature_code"]],
                defaults={
                    "access": rule_def["access"],
                    "daily_limit": rule_def["daily_limit"],
                    "monthly_limit": rule_def["monthly_limit"],
                    "per_request_limit": rule_def["per_request_limit"],
                },
            )


def unseed_catalog(apps, schema_editor):
    Plan = apps.get_model("accounts", "Plan")
    Feature = apps.get_model("accounts", "Feature")

    plan_codes = [p["code"] for p in entitlement_config.PLAN_DEFINITIONS]
    feature_codes = [code for code, _slug, _name in entitlement_config.FEATURE_DEFINITIONS]

    Plan.objects.filter(code__in=plan_codes).delete()  # cascades PlanFeatureRule
    Feature.objects.filter(code__in=feature_codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_feature_plan_userentitlement_entitlementauditlog_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_catalog, unseed_catalog),
    ]
