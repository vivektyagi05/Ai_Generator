# accounts/services/guest_identity.py
"""
Server-authoritative identity for unauthenticated ("guest") AI callers.

Design constraint from the Phase 1 spec: guest credit accounting must not
let a client bypass limits "by simply deleting/recreating a frontend
value", and must not create a permanent fake `User` row just to hold
credits.

What this uses: Django's session framework (`django.contrib.sessions`,
already installed and in MIDDLEWARE). `request.session.session_key` is:
  - generated and stored server-side (DB-backed session store by default),
  - referenced from the client only by an opaque, cryptographically signed
    cookie value that the client cannot forge or edit meaningfully,
  - NOT a value the client sets directly — calling `request.session[...] =
    x` is the only way content changes, and the key itself is assigned by
    Django, not read from client input.

This is genuinely more robust than trusting a client-supplied guest id
header/localStorage value, but it is NOT unlimited-strength anti-abuse:
a client that simply clears cookies (or opens a private window) gets a
brand-new empty session and therefore a brand-new CreditAccount with a
fresh free grant. That is a documented, known limitation of session-based
guest tracking without additional signals (IP, device fingerprint, phone
verification, etc.) — see PHASE_1_CREDIT_ENGINE.md. It is a materially
different (much narrower) hole than "the frontend sends its own balance",
which is the thing this phase is required to close.
"""

from __future__ import annotations


def get_or_create_guest_key(request) -> str:
    """
    Return a stable, server-issued session key for this guest request,
    creating the underlying Django session if one doesn't exist yet.
    """
    if not request.session.session_key:
        # Force session creation now (rather than waiting for Django to
        # lazily create it only if something gets written to it), so the
        # key is stable across the reserve -> finalize/refund lifecycle of
        # a single request and across immediate follow-up requests.
        request.session.save()
    return request.session.session_key


def identify_requester(request):
    """
    Returns (user_or_None, guest_key_or_None) — exactly one is non-None.
    """
    if request.user is not None and request.user.is_authenticated:
        return request.user, None
    return None, get_or_create_guest_key(request)
