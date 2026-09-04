# accounts/webhook_views.py
"""
PHASE 4 Step 13 — the Razorpay webhook endpoint.

    POST /api/webhooks/razorpay/

This is the ONLY code path in the project that can move a Subscription
from PENDING/PAST_DUE/GRACE_PERIOD into ACTIVE as a result of a payment
(via accounts/services/payment_service.py's payment.captured handling) --
never the checkout callback in accounts/billing_views.py, never anything
client-facing (top-level STRICT REAL-MONEY RULE: "The browser must NEVER
be the source of truth for payment success").

CSRF exemption is deliberate and safe here, NOT an oversight: Django's CSRF
protection defends session-cookie-authenticated browser requests against
being forged by another site the user's browser trusts. This endpoint is
never called by a browser and carries no session cookie at all -- its
entire trust model is the HMAC-SHA256 signature Razorpay computes over the
raw body with RAZORPAY_WEBHOOK_SECRET (verified below, first, before
anything else touches the body). A forged request without a valid
signature is rejected at that point regardless of any CSRF token; a CSRF
token could not make an unsigned/forged request trustworthy, and Razorpay's
webhook caller has no way to supply Django's CSRF token in the first place
(Step 13: "Document why CSRF exemption ... is safe only because Razorpay
signature verification replaces browser CSRF trust").
"""

from __future__ import annotations

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.services import payment_service, razorpay_client
from accounts.services.razorpay_client import RazorpayNotConfiguredError, SignatureVerificationFailed

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def razorpay_webhook_view(request):
    raw_body: bytes = request.body
    signature = request.headers.get("X-Razorpay-Signature", "")
    event_id = request.headers.get("X-Razorpay-Event-Id", "")

    if not signature:
        # No signature at all is rejected outright, same as an invalid
        # one -- never treated as "maybe legitimate" (Step 11 principle
        # applied equally to webhooks).
        logger.warning("Razorpay webhook received with no signature header")
        return HttpResponse(status=400)

    try:
        razorpay_client.verify_webhook_signature(raw_body=raw_body, signature=signature)
    except SignatureVerificationFailed:
        # Logged (without secrets) inside razorpay_client -- nothing here
        # about the body is ever trusted past this point.
        return HttpResponse(status=400)
    except RazorpayNotConfiguredError:
        logger.error("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET is not configured")
        return HttpResponse(status=503)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Razorpay webhook signature valid but body was not valid JSON")
        return HttpResponse(status=400)

    event_type = payload.get("event", "")
    if not event_id:
        # Per Razorpay's documented idempotency mechanism, X-Razorpay-Event-Id
        # is the dedup key and should always be present on a real delivery.
        # Its absence on a signature-valid request is unexpected enough
        # that we still process the event (never silently drop a
        # legitimately-signed payment notification) but cannot provide the
        # DB-level duplicate-delivery guarantee without it, so this is
        # logged loudly for operator visibility rather than assumed safe.
        logger.error(
            "Razorpay webhook missing X-Razorpay-Event-Id header; idempotency degraded",
            extra={"event_type": event_type},
        )
        event_id = f"missing-header:{payload.get('created_at', '')}:{event_type}"

    payment_service.process_webhook_event(
        event_id=event_id, event_type=event_type, raw_body=raw_body, payload=payload
    )

    # Always 200 once the signature is valid and the event has been
    # durably recorded (Step 15: ProviderEvent's uniqueness constraint is
    # what makes a Razorpay retry of this exact delivery safe) -- even if
    # _dispatch_event above logged a FAILED/IGNORED outcome internally,
    # returning a non-2xx here would only cause Razorpay to retry a
    # delivery that has already been durably, idempotently accounted for.
    return JsonResponse({"status": "ok"})
