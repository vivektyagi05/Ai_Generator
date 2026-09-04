# accounts/migrations/0011_seed_photo_feature.py
"""
PHASE 1 — GENERATOR ARCHITECTURE NORMALIZATION.

Seeds the new AI_PHOTO Feature (and its PlanFeatureRule row for every plan
in accounts/entitlement_config.PLAN_DEFINITIONS) so the Photo Generator is
registered in the entitlement catalog exactly the way the other four
generators were seeded in 0008_seed_entitlement_catalog.py.

Deliberately re-runs the SAME idempotent update_or_create seeding logic as
0008 (not a dependency on 0008's function — migrations should not import
each other) rather than a narrower "just insert AI_PHOTO" migration: this
keeps a single source of truth (accounts/entitlement_config.py) and means
re-running this migration is always safe (existing rows are updated in
place with today's config values, nothing is duplicated), matching 0008's
own documented idempotency guarantee.

Reversible: reverse_code removes exactly the AI_PHOTO Feature row (which
cascades its PlanFeatureRule rows) and nothing else — it does not touch
UserEntitlement, which may hold real grants by the time this is ever
rolled back, and it does not touch the four features 0008 already owns.
"""
from django.db import migrations

from accounts import entitlement_config


def seed_photo_feature(apps, schema_editor):
    Plan = apps.get_model("accounts", "Plan")
    Feature = apps.get_model("accounts", "Feature")
    PlanFeatureRule = apps.get_model("accounts", "PlanFeatureRule")

    photo_def = next(
        (d for d in entitlement_config.FEATURE_DEFINITIONS if d[0] == "AI_PHOTO"), None
    )
    if photo_def is None:
        # entitlement_config.py was changed after this migration was
        # written -- nothing to seed, don't guess.
        return
    code, _slug, name = photo_def

    feature, _ = Feature.objects.update_or_create(
        code=code, defaults={"name": name, "is_active": True}
    )

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
        rule_def = next(
            r
            for r in entitlement_config.plan_feature_rules_for(plan_def["code"])
            if r["feature_code"] == "AI_PHOTO"
        )
        PlanFeatureRule.objects.update_or_create(
            plan=plan,
            feature=feature,
            defaults={
                "access": rule_def["access"],
                "daily_limit": rule_def["daily_limit"],
                "monthly_limit": rule_def["monthly_limit"],
                "per_request_limit": rule_def["per_request_limit"],
            },
        )


def unseed_photo_feature(apps, schema_editor):
    Feature = apps.get_model("accounts", "Feature")
    Feature.objects.filter(code="AI_PHOTO").delete()  # cascades its PlanFeatureRule rows


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_providerevent_payment"),
    ]

    operations = [
        migrations.RunPython(seed_photo_feature, unseed_photo_feature),
    ]
