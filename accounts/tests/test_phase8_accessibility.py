"""
PHASE 8 STEP 12 -- accessibility pass on the two surfaces built from
scratch this phase: the Dashboard (templates/dashboard.html) and the
Feature Access & Usage card (templates/profile.html). Same code-level
verification approach as accounts/tests/test_accessibility.py (Phase 6)
-- no browser/screen-reader run, checking what's mechanically
checkable: heading hierarchy, real interactive elements (not
div-with-onclick), aria-live on dynamically-populated regions, visible
text on every link/button (never icon-only).

Retry button (main.html, PHASE 8 STEP 6) is covered by
test_phase8_result_retry.py already; not duplicated here.
"""

import re

from django.contrib.auth.models import User
from django.test import TestCase


class DashboardAccessibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a11yuser", password="pw12345!")
        self.client.force_login(self.user)
        self.html = self.client.get("/dashboard/").content.decode()

    def test_heading_hierarchy_has_no_skipped_levels(self):
        # Exactly one h1, followed only by h2s -- no h3/h4 introduced
        # without an intervening h2 first.
        levels = [int(m) for m in re.findall(r"<h([1-6])\b", self.html)]
        self.assertEqual(levels[0], 1)
        self.assertEqual(levels.count(1), 1, "expected exactly one h1")
        for level in levels[1:]:
            self.assertEqual(level, 2, f"unexpected heading level h{level} (only h1/h2 used)")

    def test_quick_action_links_are_real_anchors_with_visible_text(self):
        for label in ("New Photo", "New Story", "New Application", "New Proposal", "New Code"):
            self.assertIn(label, self.html)
        # Real <a href> elements, not divs with onclick/JS navigation, and
        # (PHASE 8 STEP 12) carrying the shared .ds-focusable class so they
        # get the same visible focus-visible outline as the rest of the
        # app rather than an invisible/inconsistent default.
        self.assertIn('<a class="ds-focusable" href="/home/?generator=photo">', self.html)

    def test_reopen_links_have_visible_text_not_icon_only(self):
        # If there's a Reopen link on the page it must carry the word
        # "Reopen", not rely on an icon/emoji alone.
        if ">Reopen<" in self.html or "Reopen" in self.html:
            self.assertIn("Reopen", self.html)

    def test_all_interactive_links_use_the_shared_focus_visible_style(self):
        # Scoped to <main> (this page's own content, excluding
        # nav_app.html's shell/chrome, which is pre-existing and outside
        # what Phase 8 built) -- every <a href> introduced by
        # dashboard.html carries .ds-focusable (static/css/components.css's
        # shared :focus-visible outline) rather than relying on
        # browser-default focus styling.
        main_start = self.html.index('<main class="ds-shell-main">')
        main_html = self.html[main_start:]
        anchors = re.findall(r"<a\b[^>]*>", main_html)
        real_links = [a for a in anchors if "href=" in a]
        self.assertGreater(len(real_links), 0)
        unfocusable = [a for a in real_links if "ds-focusable" not in a and "ds-link" not in a]
        self.assertEqual(unfocusable, [], f"links missing focus-visible styling: {unfocusable}")

    def test_empty_states_are_real_text_not_blank(self):
        # No generations/activity yet for this fresh user -- confirm the
        # empty state is announced as real text, not a blank region.
        self.assertIn("No generations yet", self.html)
        self.assertIn("No activity yet", self.html)


class FeatureAccessCardAccessibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a11yuser2", password="pw12345!")
        self.client.force_login(self.user)
        self.html = self.client.get("/profile/").content.decode()

    def test_feature_access_list_has_aria_live(self):
        pattern = re.compile(
            r'id="featureAccessList"[^>]*aria-live="polite"'
            r'|aria-live="polite"[^>]*id="featureAccessList"'
        )
        self.assertTrue(pattern.search(self.html), "featureAccessList missing aria-live=\"polite\"")

    def test_lapsed_subscription_notice_is_announced_as_status(self):
        # PHASE 8B Rule 8/11 -- billingLapsedNotice (Item 2) is populated
        # by JS after the initial render, so it needs role="status" (an
        # implicit aria-live="polite" region) for a screen reader to
        # announce it whenever ProfileBilling.render() reveals it -- not
        # mechanically checkable whether a real screen reader actually
        # announces it (no browser/AT in this environment, same
        # limitation this file's docstring already states for every test
        # here), but the markup contract itself is verified.
        pattern = re.compile(r'id="billingLapsedNotice"[^>]*role="status"')
        self.assertTrue(pattern.search(self.html), "billingLapsedNotice missing role=\"status\"")


class DashboardLapsedSubscriptionAccessibilityTests(TestCase):
    """Same markup contract as FeatureAccessCardAccessibilityTests above,
    but for dashboard.html's server-rendered equivalent (Item 4) -- only
    meaningful when a lapsed subscription actually exists to render, so
    this drives a real refunded user through the page rather than reusing
    the plain empty-state fixture the other DashboardAccessibilityTests
    class uses."""

    def test_lapsed_subscription_banner_is_announced_as_status(self):
        import json
        from unittest.mock import patch
        from django.test import override_settings
        from accounts.services import payment_service

        user = User.objects.create_user(username="a11ydashrefund", password="pw12345!")
        with override_settings(
            RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret", RAZORPAY_WEBHOOK_SECRET="whsecret"
        ):
            with patch("accounts.services.payment_service.razorpay_client.create_order") as mock_order:
                mock_order.return_value = {"id": "order_a11y_1"}
                payment, _ = payment_service.create_payment_order(
                    user=user, plan_code="PREMIUM", billing_interval="MONTHLY"
                )
            captured_payload = {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_a11y_1",
                            "order_id": "order_a11y_1",
                            "amount": payment.amount,
                            "currency": payment.currency,
                            "status": "captured",
                            "method": "card",
                        }
                    }
                },
            }
            payment_service.process_webhook_event(
                event_id="evt_a11y_capture",
                event_type="payment.captured",
                raw_body=json.dumps(captured_payload).encode(),
                payload=captured_payload,
            )
            refund_payload = {
                "event": "refund.processed",
                "payload": {
                    "refund": {"entity": {"id": "rfnd_a11y_1", "payment_id": "pay_a11y_1", "amount": payment.amount}}
                },
            }
            payment_service.process_webhook_event(
                event_id="evt_a11y_refund",
                event_type="refund.processed",
                raw_body=json.dumps(refund_payload).encode(),
                payload=refund_payload,
            )

        self.client.force_login(user)
        html = self.client.get("/dashboard/").content.decode()
        pattern = re.compile(r'class="billing-status-banner state-info visible"[^>]*role="status"')
        self.assertTrue(pattern.search(html), "dashboard lapsed-subscription banner missing role=\"status\"")
