# accounts/billing_views.py
"""
PHASE 4 Step 8/10/22 — authenticated billing APIs for checkout.

Identity always comes from the authenticated session (request.user) --
never a user_id/payment_id supplied by the client (Step 23: no IDOR,
mirroring accounts/subscription_views.py's existing pattern). No endpoint
here accepts amount, currency, plan price, payment status, or subscription
status from the client body -- every one of those is either resolved
server-side (accounts/billing_config.py) or cryptographically verified
(accounts/services/razorpay_client.py).

Deliberately absent, same as subscription_views.py:

    POST /api/billing/activate-premium/
    POST /api/billing/set-status/
    POST /api/billing/set-amount/

No such endpoints exist anywhere in this project (Step 22). The only way a
Subscription becomes ACTIVE from a payment is
accounts/services/payment_service.py's webhook-driven
_handle_payment_captured -- never a client-facing endpoint call.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Payment, Subscription
from accounts.services import payment_service
from accounts.services.exceptions import (
    DuplicateLiveSubscriptionError,
    PaymentNotFoundError,
    PaymentVerificationError,
    UnpayablePlanError,
)
from accounts.services.razorpay_client import RazorpayNotConfiguredError, SignatureVerificationFailed


def _serialize_payment(payment: Payment) -> dict:
    return {
        "order_id": payment.order_id,
        "status": payment.status,
        "amount": payment.amount,
        "currency": payment.currency,
        "plan": payment.plan.code,
        "billing_interval": payment.billing_interval,
    }


@require_POST
@login_required
def create_order_view(request):
    """
    POST /api/billing/create-order/
    body: {"plan": "PREMIUM", "billing_interval": "MONTHLY"}

    Resolves the payable amount server-side and creates a Razorpay Order.
    The response contains ONLY values safe to hand to Razorpay's
    checkout.js in the browser (Step 10) -- never a secret.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    plan_code = str(body.get("plan", "PREMIUM"))
    billing_interval = str(body.get("billing_interval", Subscription.BillingInterval.MONTHLY))

    try:
        payment, checkout_info = payment_service.create_payment_order(
            user=request.user, plan_code=plan_code, billing_interval=billing_interval
        )
    except UnpayablePlanError as exc:
        return JsonResponse({"error": str(exc), "code": "INVALID_PAYMENT"}, status=400)
    except DuplicateLiveSubscriptionError as exc:
        return JsonResponse({"error": str(exc), "code": "PAYMENT_IN_PROGRESS"}, status=409)
    except RazorpayNotConfiguredError:
        # Deterministic configuration problem, not a transient outage --
        # "code" lets the frontend stop offering "Try Again" and show a
        # config-specific message, without ever naming the missing env
        # var or exposing internals (Phase C/J).
        return JsonResponse(
            {"error": "Secure payments are temporarily unavailable.", "code": "BILLING_NOT_CONFIGURED"},
            status=503,
        )

    return JsonResponse(checkout_info)


@require_POST
@login_required
def verify_payment_view(request):
    """
    POST /api/billing/verify-payment/
    body: {"order_id": "...", "payment_id": "...", "signature": "..."}

    Called by the frontend's Razorpay checkout.js success handler. Verifies
    the signature server-side and cross-checks the payment against
    Razorpay's own records (Step 11/12). This endpoint NEVER activates a
    subscription itself -- see payment_service.verify_checkout_callback's
    docstring for why. It exists only to let the UI show a trustworthy
    "payment received" state instead of a bare, unverifiable redirect.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)

    order_id = body.get("order_id")
    payment_id = body.get("payment_id")
    signature = body.get("signature")
    if not order_id or not payment_id or not signature:
        return JsonResponse({"error": "order_id, payment_id, and signature are required"}, status=400)

    try:
        payment = payment_service.verify_checkout_callback(
            user=request.user, order_id=str(order_id), payment_id=str(payment_id), signature=str(signature)
        )
    except PaymentNotFoundError:
        return JsonResponse({"error": "payment not found", "code": "PAYMENT_NOT_FOUND"}, status=404)
    except SignatureVerificationFailed:
        return JsonResponse(
            {"error": "signature verification failed", "code": "PAYMENT_VERIFICATION_FAILED"}, status=400
        )
    except PaymentVerificationError:
        return JsonResponse(
            {"error": "payment did not match the expected order", "code": "PAYMENT_VERIFICATION_FAILED"},
            status=400,
        )
    except RazorpayNotConfiguredError:
        return JsonResponse(
            {"error": "Secure payments are temporarily unavailable.", "code": "BILLING_NOT_CONFIGURED"},
            status=503,
        )

    return JsonResponse(_serialize_payment(payment))


@require_GET
@login_required
def payment_status_view(request, order_id: str):
    """
    GET /api/billing/payment-status/<order_id>/ -- lets the frontend poll
    for the webhook-driven CAPTURED transition after checkout. Only ever
    returns the caller's own payment (Step 23: no IDOR) -- a nonexistent
    or someone-else's order_id both resolve to the same 404, never
    revealing which.
    """
    payment = Payment.objects.filter(user=request.user, order_id=order_id).select_related("plan").first()
    if payment is None:
        return JsonResponse({"error": "payment not found"}, status=404)
    return JsonResponse(_serialize_payment(payment))
