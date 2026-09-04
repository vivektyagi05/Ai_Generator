# accounts/credit_config.py
"""
Central configuration for the Phase 1 credit engine.

Every number in this file is a PLACEHOLDER business value, not something
established by an earlier phase's product/pricing decision (none exists in
this codebase as of Phase 1). They are intentionally isolated here — not
hardcoded in views/services — so a real pricing decision later only means
changing this file (or the environment), not touching the accounting logic
in accounts/services/credit_service.py.

Every value can be overridden from Django settings (and therefore from the
environment, via the existing python-dotenv `.env` loading in
AI_GENERATORS/settings.py) without code changes:

    CREDIT_INITIAL_FREE_GRANT = 50
    CREDIT_MONTHLY_FREE_GRANT = 20
    CREDIT_PER_1000_TOKENS = 1
    CREDIT_MINIMUM_CHARGE = 1
    CREDIT_ESTIMATED_CHARS_PER_TOKEN = 4
    CREDIT_DEFAULT_MAX_COMPLETION_TOKENS = 1024

============================================================================
WHAT "1 CREDIT" MEANS RIGHT NOW (Step 7 / Step 8)
============================================================================
Text/code generation goes through Groq's OpenAI-compatible chat-completions
endpoint (AI_GENERATORS/api_views.py, model "openai/gpt-oss-20b" as of
PHASE 8A -- see that module's GROQ_MODEL comment for why), whose
response includes a standard OpenAI-style `usage` object (prompt_tokens /
completion_tokens / total_tokens) when the request is non-streaming, which
is how this endpoint calls it. So real token counts ARE available for text
features and are what usage is billed on — see
credit_service.calculate_actual_cost().

"1 credit" is defined here as an INTERNAL ACCOUNTING UNIT, not a real-money
or real-provider-cost figure:

    1 credit == CREDIT_PER_1000_TOKENS applied per 1000 tokens, rounded up,
    with a minimum charge per successful request.

This is a documented placeholder ratio, not a claim about what Groq
actually charges. Replace CREDIT_PER_1000_TOKENS (and the pricing model
entirely, if needed) once Phase 0/product has set real numbers.

============================================================================
PHASE 1 — GENERATOR ARCHITECTURE NORMALIZATION: image generation ("photo")
============================================================================
The image provider this project calls (see AI_GENERATORS/api_views.py's
_call_image_provider()) does not expose a token/usage figure at all — it's
a prompt-to-image endpoint, not a chat-completions one. Charging it on the
same "tokens -> credits" formula as text would be exactly the "pretend
token accounting for a provider that doesn't expose it" mistake this
module's own docstring has always forbidden, so photo instead gets a flat,
documented placeholder cost per successful generation:
IMAGE_GENERATION_CREDIT_COST. This reuses the credit engine's existing
"usage unavailable -> charge exactly what was reserved" path
(accounts.services.ai_usage_service.complete_success) rather than adding a
second charging mechanism — see estimate_reservation_credits() below.
"""

from django.conf import settings

# ── Free grants (Step 10 infrastructure only — no UI, no scheduler wired) ──

# Granted once, the first time a CreditAccount is created for a user or a
# guest session.
INITIAL_FREE_CREDITS: int = int(getattr(settings, "CREDIT_INITIAL_FREE_GRANT", 50))

# Granted at most once per (account, calendar month) by
# credit_service.grant_monthly_credits() — see that function's docstring
# for why this is infrastructure-only and not on an automatic schedule.
MONTHLY_FREE_CREDITS: int = int(getattr(settings, "CREDIT_MONTHLY_FREE_GRANT", 20))

# ── Cost model (Step 7 / Step 8) ────────────────────────────────────────

# Internal accounting ratio: credits charged per 1000 tokens (rounded up).
CREDIT_PER_1000_TOKENS: int = int(getattr(settings, "CREDIT_PER_1000_TOKENS", 1))

# A successful request always costs at least this many credits, even if
# token usage rounds to less (covers per-request overhead).
MINIMUM_CREDIT_CHARGE: int = int(getattr(settings, "CREDIT_MINIMUM_CHARGE", 1))

# Used ONLY to estimate the *reservation* before the provider is called
# (we don't know actual tokens yet). A conservative, documented heuristic
# — not billed on. Actual billing always uses the real `usage` the
# provider returns (or, if that's unavailable, the reservation itself —
# see credit_service.calculate_actual_cost()).
ESTIMATED_CHARS_PER_TOKEN: int = int(getattr(settings, "CREDIT_ESTIMATED_CHARS_PER_TOKEN", 4))

# Matches the fixed `max_tokens` the provider payload already sends
# (AI_GENERATORS/api_views.py). Kept as a named constant here so the two
# stay in sync deliberately rather than by coincidence.
DEFAULT_MAX_COMPLETION_TOKENS: int = int(
    getattr(settings, "CREDIT_DEFAULT_MAX_COMPLETION_TOKENS", 1024)
)

# Flat, documented placeholder cost for one successful image generation
# ("photo" feature) — see the module docstring section above for why this
# is flat rather than token-derived. Overridable the same way as every
# other number in this file: CREDIT_IMAGE_GENERATION_COST in settings/env.
IMAGE_GENERATION_CREDIT_COST: int = int(getattr(settings, "CREDIT_IMAGE_GENERATION_COST", 5))

# Feature slugs billed with a flat per-request cost instead of the
# token-based formula. Currently just "photo"; kept as a set (not an
# if-statement inline in estimate_reservation_credits) so a second
# non-token-metered feature added later is a one-line change here, not a
# new branch buried in the reservation function itself.
FLAT_COST_FEATURES: frozenset[str] = frozenset({"photo"})


def estimate_reservation_credits(prompt: str, *, feature: str | None = None) -> int:
    """
    Pre-call estimate used only to decide how much to RESERVE before the
    provider has actually run. Always rounds up / never under-reserves for
    token-billed features; for a flat-cost feature (see FLAT_COST_FEATURES)
    this simply returns that fixed cost, since there is no token count to
    estimate from in the first place.

    `feature` is optional and defaults to the pre-Phase-1 token-based
    estimate for any caller that doesn't pass one — existing behavior for
    chat/code/story/application/proposal is unchanged either way.
    """
    if feature in FLAT_COST_FEATURES:
        return IMAGE_GENERATION_CREDIT_COST

    prompt_len = len(prompt or "")
    estimated_prompt_tokens = -(-prompt_len // ESTIMATED_CHARS_PER_TOKEN)  # ceil div
    estimated_total_tokens = estimated_prompt_tokens + DEFAULT_MAX_COMPLETION_TOKENS
    return tokens_to_credits(estimated_total_tokens)


def tokens_to_credits(total_tokens: int) -> int:
    """Round-up token->credit conversion with a minimum charge floor."""
    if total_tokens <= 0:
        return MINIMUM_CREDIT_CHARGE
    credits = -(-total_tokens * CREDIT_PER_1000_TOKENS // 1000)  # ceil div
    return max(credits, MINIMUM_CREDIT_CHARGE)
