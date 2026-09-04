"""
Rate Limiting — Cooldown + sliding-window throttling for OTP-issuing and
OTP-verifying endpoints, built on Django's cache framework.

Why the cache framework and not Celery/Redis: this project has no existing
dependency on either, and the audit explicitly asks for a solution that
doesn't require standing up external infrastructure. Django's cache
framework (`django.core.cache.cache`) is used with whatever CACHES backend
is configured — LocMemCache by default, which is enough to stop a single
dev/staging process from being hammered.

IMPORTANT PRODUCTION NOTE (see EMAIL_SETUP.md): LocMemCache is per-process.
If this app runs multiple gunicorn worker processes (it does, per Procfile),
each worker has its OWN counters, so the *effective* limit in production is
roughly (configured limit) x (worker count), and a client can partially
evade a single worker's cooldown by getting load-balanced to another. This
is documented as a known limitation, not hidden. For airtight production
rate limiting, point CACHES at a shared backend (e.g. a Redis cache) — this
module needs no code changes to benefit from that, since it only talks to
Django's cache API.
"""

from django.core.cache import cache

# ── Tunables ──────────────────────────────────────────────────────────────

OTP_SEND_COOLDOWN_SECONDS = 30          # per-email: min gap between sends
OTP_SEND_WINDOW_SECONDS = 60 * 60       # per-email: rolling window for the cap below
OTP_SEND_MAX_PER_WINDOW = 5             # per-email: max sends per window

OTP_VERIFY_MAX_ATTEMPTS = 5             # per-OTP: max wrong guesses before invalidation

IP_WINDOW_SECONDS = 10 * 60             # per-IP: rolling window for the cap below
IP_MAX_PER_WINDOW = 20                  # per-IP: max auth-related requests per window

# PHASE 9 — Step 3: failed-login throttle, per account (email). The
# per-IP cap above (via check_ip_throttle(request, "login")) already
# bounds a single source hammering the endpoint, but doesn't stop a
# distributed attempt (many IPs) targeting ONE account's password. This
# is deliberately keyed by email, not IP, and only counts on a *wrong*
# password (a correct login never increments it) so it doesn't add
# friction for a legitimate user who just isn't rate-limited elsewhere.
LOGIN_FAIL_WINDOW_SECONDS = 15 * 60     # per-email: rolling window for the cap below
LOGIN_FAIL_MAX_PER_WINDOW = 8           # per-email: max failed attempts per window

# ── AI endpoint (/api/ai/) — PHASE 1A Step 3 ────────────────────────────────
#
# Design: separate baselines per policy tier (Step 1). This rate limiter
# does not vary by plan even now that Phase 2 adds a real entitlement
# engine (accounts/services/entitlement_service.py) -- rate limiting and
# entitlement/quota are deliberately independent gates (see PHASE_2_
# ENTITLEMENT_ENGINE.md "Quota vs. credit vs. rate limit"), and Premium
# still shares AI_AUTH_MAX_PER_WINDOW with authenticated-free here. A
# plan-aware rate-limit tier would be a legitimate future change, but it's
# a change to THIS module's own numbers, not something Phase 2 needed to
# touch -- see accounts/rate_limit.py:check_ai_rate_limit()'s docstring
# and PHASE_1_CREDIT_ENGINE.md.
#
# Guests get TWO independent caps, not one:
#   - per guest session (a session is server-issued, see guest_identity.py)
#   - per client IP, summed across however many guest sessions that IP has
#     created (Step 9: a session alone is not strong anti-abuse, since
#     clearing cookies gets a brand-new session with a brand-new free grant
#     -- the IP cap is what actually bounds that).
# Authenticated users only get the per-user cap: their identity is already
# durable (a login), so there's no equivalent "create a new one for free"
# hole to additionally bound by IP.
AI_GUEST_SESSION_WINDOW_SECONDS = 60 * 60     # 1 hour
AI_GUEST_SESSION_MAX_PER_WINDOW = 8           # per guest session, per hour
AI_GUEST_IP_WINDOW_SECONDS = 60 * 60          # 1 hour
AI_GUEST_IP_MAX_PER_WINDOW = 20               # per IP, per hour (covers session-cycling)

AI_AUTH_WINDOW_SECONDS = 60 * 60              # 1 hour
AI_AUTH_MAX_PER_WINDOW = 40                   # per authenticated user, per hour


def get_client_ip(request) -> str:
    """Best-effort client IP extraction (trusts X-Forwarded-For's first hop,
    which is fine here since this is used for coarse throttling, not auth)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _incr_window(key: str, window_seconds: int) -> int:
    """Increment a counter with a TTL, creating it if absent. Returns the new count."""
    try:
        # cache.incr raises ValueError if the key doesn't exist yet.
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window_seconds)
        return 1


def check_send_cooldown(email: str, purpose: str) -> bool:
    """
    True if an OTP may be sent to (email, purpose) right now; False if still
    inside the cooldown window. Recording a send is a separate call
    (`record_send`) so a check never has the side effect of starting a
    cooldown the caller then fails to honor.
    """
    key = f"otp:cooldown:{purpose}:{email}"
    return cache.get(key) is None


def record_send(email: str, purpose: str) -> None:
    """Record that an OTP send just happened, starting the cooldown and
    incrementing the rolling-window counter."""
    cache.set(f"otp:cooldown:{purpose}:{email}", True, OTP_SEND_COOLDOWN_SECONDS)
    _incr_window(f"otp:window:{purpose}:{email}", OTP_SEND_WINDOW_SECONDS)


def check_send_window(email: str, purpose: str) -> bool:
    """True if (email, purpose) is still under its per-window send cap."""
    count = cache.get(f"otp:window:{purpose}:{email}", 0)
    return count < OTP_SEND_MAX_PER_WINDOW


def check_ip_throttle(request, action: str) -> bool:
    """True if this client IP is still under its per-window request cap for
    the given action. Increments the counter as a side effect (every check
    counts as a request, which is the desired throttle semantic)."""
    ip = get_client_ip(request)
    key = f"otp:ip:{action}:{ip}"
    count = _incr_window(key, IP_WINDOW_SECONDS)
    return count <= IP_MAX_PER_WINDOW


def check_login_throttle(email: str) -> bool:
    """True if (email) is still under its per-window failed-login cap.
    Read-only -- does not increment. Call before attempting authentication
    so an already-locked-out account doesn't pay the bcrypt/argon2 hashing
    cost of a doomed check_password() call on every retry."""
    count = cache.get(f"login:fail:{email}", 0)
    return count < LOGIN_FAIL_MAX_PER_WINDOW


def record_failed_login(email: str) -> None:
    """Record a wrong-credentials attempt for (email). Deliberately called
    for every failed attempt regardless of whether the email maps to a
    real account -- gating this on account existence would let an
    attacker fingerprint which emails are registered by noticing which
    ones start rate-limiting after N tries and which never do."""
    _incr_window(f"login:fail:{email}", LOGIN_FAIL_WINDOW_SECONDS)


def check_ai_rate_limit(request, *, user, guest_key: str | None) -> tuple[bool, int, str]:
    """
    Server-side rate limit for /api/ai/ (Step 3). Frontend throttling is
    NOT security -- this is the actual enforcement point, called from
    AI_GENERATORS/api_views.py after identity resolution and before any
    request body parsing (a client under its cap is still fully validated
    afterward; a client over its cap never even gets its JSON parsed).

    Every call to this function that resolves to "allowed" also counts
    toward the limit (matches the existing check_ip_throttle semantic in
    this module) -- so a burst of requests that each separately fail
    validation, insufficient-credits, or provider errors still consumes
    rate-limit budget. That's intentional: an attacker who only wants to
    exhaust rate-limit budget (denial of service against the endpoint)
    shouldn't get a cheaper path just by sending requests designed to fail
    downstream.

    Returns (allowed, retry_after_seconds, scope):
      - allowed: False means the caller must be refused with HTTP 429.
      - retry_after_seconds: suggested Retry-After header value.
      - scope: which cap was hit -- "authenticated", "guest_session", or
        "guest_ip" -- for logging/metrics, never exposed verbatim in the
        HTTP response (Step 3: don't leak sensitive information about the
        limiting mechanism to the client).
    """
    if user is not None:
        key = f"ai:rl:user:{user.pk}"
        count = _incr_window(key, AI_AUTH_WINDOW_SECONDS)
        if count > AI_AUTH_MAX_PER_WINDOW:
            return False, AI_AUTH_WINDOW_SECONDS, "authenticated"
        return True, 0, "authenticated"

    # Guest: both caps are checked (and both counters incremented) on every
    # call, so evaluating one doesn't mask the other being exceeded.
    session_count = _incr_window(f"ai:rl:guestsession:{guest_key}", AI_GUEST_SESSION_WINDOW_SECONDS)
    ip = get_client_ip(request)
    ip_count = _incr_window(f"ai:rl:guestip:{ip}", AI_GUEST_IP_WINDOW_SECONDS)

    if session_count > AI_GUEST_SESSION_MAX_PER_WINDOW:
        return False, AI_GUEST_SESSION_WINDOW_SECONDS, "guest_session"
    if ip_count > AI_GUEST_IP_MAX_PER_WINDOW:
        return False, AI_GUEST_IP_WINDOW_SECONDS, "guest_ip"
    return True, 0, "guest"
