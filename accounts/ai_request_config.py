# accounts/ai_request_config.py
"""
PHASE 1A — Central configuration for server-side AI *request* validation
(Step 4), as distinct from accounts/credit_config.py which prices requests
that already passed validation.

Every limit here is enforced server-side in AI_GENERATORS/api_views.py
BEFORE a credit reservation is attempted. The frontend never re-implements
these numbers — AI_GENERATORS/views.py:create() passes this same module's
values into templates/main.html via Django's `json_script` template filter
(see the `ai-request-limits` script tag), and templates/main.html reads
them at runtime instead of hardcoding a second copy. If a limit needs to
change, change it here only.

All values are overridable from Django settings / the environment, exactly
like accounts/credit_config.py:

    AI_MAX_PROMPT_CHARS = 4000
    AI_MAX_BODY_BYTES = 20000
    AI_ALLOWED_FEATURES = "chat,code,story,application,proposal,photo"

PHASE 1 — GENERATOR ARCHITECTURE NORMALIZATION: "photo" added to the
default allowed-features list so the Photo Generator's request now goes
through the exact same validation this module already enforces for the
other four generators (feature must be recognized here before it reaches
the entitlement/credit engines) -- see AI_GENERATORS/api_views.py.
"""

from __future__ import annotations

import re

from django.conf import settings

# A request whose raw body exceeds this many bytes is rejected with 413
# before it is even JSON-decoded. Generously above MAX_PROMPT_CHARS to
# leave room for JSON structure/escaping, well below anything that could
# meaningfully strain the process.
MAX_BODY_BYTES: int = int(getattr(settings, "AI_MAX_BODY_BYTES", 20_000))

# A prompt longer than this is rejected with 400 *before* a reservation is
# made (Step 4: never rely on frontend validation, never charge for a
# request the server itself refuses to run).
MAX_PROMPT_CHARS: int = int(getattr(settings, "AI_MAX_PROMPT_CHARS", 4_000))

# Only these feature slugs are accepted. Anything else in the request body
# is a 400, not a silent fallback -- silently coercing an unrecognized
# feature to "chat" would hide client bugs and defeat auditing (AIUsage.
# feature is used for per-feature reporting). Omitting `feature` entirely
# still defaults to "chat" for backward compatibility with existing
# frontend call sites that don't send it.
_DEFAULT_ALLOWED_FEATURES = "chat,code,story,application,proposal,photo"
ALLOWED_FEATURES: frozenset[str] = frozenset(
    f.strip()
    for f in getattr(settings, "AI_ALLOWED_FEATURES", _DEFAULT_ALLOWED_FEATURES).split(",")
    if f.strip()
)
DEFAULT_FEATURE: str = "chat"

# Only this content type is accepted for the JSON body. request.content_type
# already strips any ";charset=..." suffix, so this is an exact match.
ALLOWED_CONTENT_TYPE: str = "application/json"

# request_id must be a short opaque token -- this is an idempotency key,
# never executed, never interpolated into a query, but still bounded and
# charset-restricted so a hostile value can't blow past AIUsage.request_id's
# max_length=64 (which would otherwise surface as an uncaught IntegrityError
# / DataError deep in ai_usage_service instead of a clean 400) or be used to
# smuggle unexpected bytes through logs/metadata.
REQUEST_ID_MAX_LENGTH: int = 64
_REQUEST_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"
REQUEST_ID_RE = re.compile(_REQUEST_ID_PATTERN)


def as_frontend_context() -> dict:
    """
    JSON-serializable snapshot of the limits the frontend needs to know
    about (client-side validation is a UX nicety only -- Step 4 -- the
    server re-checks everything regardless). Rendered into templates/
    main.html with `{{ ai_request_limits|json_script:"ai-request-limits" }}`
    so the frontend reads these numbers instead of hardcoding a second copy.
    """
    return {
        "maxPromptChars": MAX_PROMPT_CHARS,
        "maxBodyBytes": MAX_BODY_BYTES,
        "allowedFeatures": sorted(ALLOWED_FEATURES),
    }
