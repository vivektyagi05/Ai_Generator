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
