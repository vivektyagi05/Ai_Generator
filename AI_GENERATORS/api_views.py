# AI_GENERATORS/api_views.py
"""
PHASE 1A credit-engine + security-hardening integration.

Contract preserved for the existing frontend (templates/main.html, 4 call
sites): POST {"prompt": "..."} to /api/ai/, get back {"result": "..."} on
200, or {"error": "..."} on a non-2xx status. Everything Phase 1 added
(request_id, credits_charged, credits_available, feature) stayed additive
JSON; the frontend now also sends X-CSRFToken and a stable request_id via
the canonical `aiRequest()` helper in templates/main.html.

Request lifecycle (PHASE 2 updates this -- entitlement/quota now run before
rate limiting and before any credit reservation, per PHASE_2_ENTITLEMENT_
ENGINE.md Step 11):

    HTTP method validation
        |
    CSRF validation            -- Django's CsrfViewMiddleware; this view is
        |                          NOT @csrf_exempt anymore. See
        |                          PHASE_1_CREDIT_ENGINE.md "CSRF policy".
    identity resolution        -- accounts.services.guest_identity
        |
    request pre-parse          -- content-type / body size / JSON / feature
        |                          (just enough to know `feature` -- see
        |                          _check_access_policy()'s call site)
    entitlement + quota        -- _check_access_policy() -> accounts.
        |                          services.entitlement_service
        |                          .get_access_decision()
    rate limit                 -- accounts.rate_limit.check_ai_rate_limit()
        |
    request validation         -- prompt length / request_id shape (this
        |                          module + accounts.ai_request_config)
    idempotency                -- accounts.services.ai_usage_service
        |                          .check_existing_usage()
    credit reservation         -- accounts.services.ai_usage_service
        |                          .begin_request() -> credit_service.reserve()
    provider                   -- _call_provider() (Groq)
        |
    finalize/refund            -- ai_usage_service.complete_success()/
        |                          complete_failure() -> credit_service
    quota usage record          -- entitlement_service.record_usage() (only
        |                          on provider success -- see call site)
    usage ledger                  (AIUsage row, updated at every stage above)
        |
    response

Known limitations (documented, not hidden -- see PHASE_1_CREDIT_ENGINE.md /
PHASE_2_ENTITLEMENT_ENGINE.md):
  - Rate limiting AND entitlement quota counters both use Django's cache
    framework (LocMemCache by default), which is per-process -- see
    accounts/rate_limit.py's module docstring and entitlement_service.py's
    "Usage counters" section.
  - Guest identity is session-based; clearing cookies gets a new session
    and a new free grant -- bounded (not eliminated) by the per-IP guest
    cap, see accounts.rate_limit.check_ai_rate_limit().
  - Premium has a real, server-authoritative entitlement engine as of
    Phase 2 (accounts.services.entitlement_service), but no paid activation
    path yet -- Premium entitlements can only be created via an
    admin-audited override or a test fixture (Phase 3/4 add real payment).
"""

import json
import time
import urllib.parse
import uuid

import requests
from django.conf import settings
from django.http import JsonResponse

from accounts import ai_request_config, rate_limit
from accounts.services import ai_usage_service, entitlement_service, guest_identity
from accounts.services.ai_usage_service import ValidationFailure
from accounts.services.exceptions import DuplicateRequestError, InsufficientCreditsError, UnknownFeatureError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# PHASE 8A — ROOT CAUSE FIX (see PHASE_8A_ROOT_CAUSE.md).
#
# "llama-3.1-8b-instant" was deprecated by Groq on 2026-06-17 and shut down
# on 2026-08-16 (https://console.groq.com/docs/deprecations). Every request
# sent to a decommissioned model ID returns HTTP 400 with
# {"error": {"code": "model_decommissioned", ...}} — this is exactly the
# generic "AI provider returned an error." the user was seeing for every
# Groq-backed generator (Story/Application/Proposal/Code), while Photo kept
# working because it calls Pollinations, an entirely separate provider that
# was never affected. Groq's recommended replacement for this model is
# "openai/gpt-oss-20b" (same "fast/small" tier as the old 8B-instant model).
GROQ_MODEL = "openai/gpt-oss-20b"
PROVIDER_NAME = "groq"

_PROVIDER_TIMEOUT_SECONDS = 15
_PROVIDER_MAX_ATTEMPTS = 2

# ── PHASE 1 — GENERATOR ARCHITECTURE NORMALIZATION: image provider ─────────
#
# Same public endpoint templates/main.html used to call directly from the
# browser (see PHASE_0_FORENSIC_AUDIT.md "Photo Generator" finding), moved
# server-side. Pollinations' prompt-to-image endpoint takes no API
# key/secret -- there is nothing to hide from the browser here -- but it is
# still called from the server, not the client, so that entitlement/credit
# reservation stays authoritative (Step 4 of this phase's task: "if the
# existing provider requires no secret, it must still be called server-side
# so usage control remains authoritative").
IMAGE_PROVIDER_NAME = "pollinations"
IMAGE_MODEL = "flux"
_IMAGE_PROVIDER_BASE_URL = "https://image.pollinations.ai/prompt/"
_IMAGE_WIDTH = 1024
_IMAGE_HEIGHT = 1024

# Image generation is slower than a short chat completion, so this gets its
# own (longer) timeout rather than reusing _PROVIDER_TIMEOUT_SECONDS. Only
# one attempt -- unlike text's retry-twice -- because a retried image
# generation is a second, distinct multi-second provider call, not a cheap
# retry, and the credit reservation already guarantees the user is never
# charged for a failed attempt (see complete_failure()).
_IMAGE_PROVIDER_TIMEOUT_SECONDS = 30
_IMAGE_PROVIDER_MAX_ATTEMPTS = 1


# ============================================================================
# Step 1 — AI access policy
# ============================================================================
#
# Explicit, not implicit: this function is the single place that decides
# whether an identity is allowed onto the AI endpoint at all, so the policy
# is legible and extendable without hunting through the view body.
#
#   Guest        -> allowed (limited demo access; bounded by
#                    accounts.rate_limit's guest caps, not by identity).
#   Authenticated -> allowed (bounded by accounts.rate_limit's per-user cap).
#   Premium      -> NOT implemented in this phase (see module docstring /
#                    task STOP CONDITION). When entitlements exist, this is
#                    the function that will branch on them -- e.g. to lift
#                    the rate-limit tier or unlock premium-only features --
#                    without touching the request lifecycle around it.
#
# Returns None if the request may proceed, or a JsonResponse to return
# immediately (kept symmetrical with the rest of the view's early-return
# style).
#
# PHASE 2: this is no longer a stub. It calls the single canonical
# entitlement service (accounts/services/entitlement_service.py) — this
# function does NOT itself decide anything about plans/features/quota, it
# only translates that service's AccessDecision into the view's
# early-return / HTTP-status idiom. See PHASE_2_ENTITLEMENT_ENGINE.md
# "API response contract" (Step 23) for the status-code mapping below.
_DENIAL_STATUS = {
    entitlement_service.DenialReason.AUTHENTICATION_REQUIRED: 401,
    entitlement_service.DenialReason.FEATURE_DISABLED: 403,
    entitlement_service.DenialReason.PLAN_NOT_ALLOWED: 403,
    entitlement_service.DenialReason.ENTITLEMENT_INACTIVE: 403,
    entitlement_service.DenialReason.ENTITLEMENT_EXPIRED: 403,
    entitlement_service.DenialReason.GUEST_RESTRICTION: 403,
    entitlement_service.DenialReason.QUOTA_EXCEEDED: 429,
}
_DENIAL_MESSAGE = {
    entitlement_service.DenialReason.AUTHENTICATION_REQUIRED: "Please sign in to use this feature.",
    entitlement_service.DenialReason.FEATURE_DISABLED: "This feature is not available.",
    entitlement_service.DenialReason.PLAN_NOT_ALLOWED: "This feature is not available on your current plan.",
    entitlement_service.DenialReason.ENTITLEMENT_INACTIVE: "Your plan is not currently active.",
    entitlement_service.DenialReason.ENTITLEMENT_EXPIRED: "Your plan has expired.",
    entitlement_service.DenialReason.GUEST_RESTRICTION: "Daily guest limit reached. Please sign in to continue.",
    entitlement_service.DenialReason.QUOTA_EXCEEDED: "You've reached your usage limit for this feature.",
}


def _check_access_policy(user, guest_key, feature):
    try:
        decision = entitlement_service.get_access_decision(user, guest_key, feature)
    except UnknownFeatureError:
        return JsonResponse(
            {"success": False, "error": {"code": "FEATURE_DISABLED", "message": "Invalid feature."}},
            status=403,
        )

    if decision.allowed:
        return None

    status = _DENIAL_STATUS.get(decision.reason, 403)
    message = _DENIAL_MESSAGE.get(decision.reason, "Access denied.")
    return JsonResponse(
        {"success": False, "error": {"code": decision.reason, "message": message}}, status=status
    )


def _clean_feature(raw):
    """
    Returns (feature, error_response_or_None). `raw` absent entirely keeps
    the pre-Phase-1A default ("chat") for backward compatibility with
    existing frontend call sites that don't send a `feature` field yet. A
    `raw` value that IS present but unrecognized is now a rejection (400),
    not a silent coercion -- see accounts/ai_request_config.py.
    """
    if raw is None or raw == "":
        return ai_request_config.DEFAULT_FEATURE, None
    if isinstance(raw, str) and raw in ai_request_config.ALLOWED_FEATURES:
        return raw, None
    return None, JsonResponse({"error": "Invalid feature."}, status=400)


def _clean_request_id(raw):
    """
    Returns (request_id, error_response_or_None). A caller-supplied
    request_id must be a short opaque token (see ai_request_config for the
    exact shape) -- anything else is rejected before it can reach
    AIUsage.request_id (max_length=64) or any log line. Omitted entirely ->
    server generates one (Step 1 legacy behavior for callers that don't
    implement idempotency themselves).
    """
    if raw is None or raw == "":
        return uuid.uuid4().hex, None
    if isinstance(raw, str) and ai_request_config.REQUEST_ID_RE.match(raw):
        return raw, None
    return None, JsonResponse({"error": "Invalid request_id."}, status=400)


def _provider_error_response(code, message, *, status, retryable, request_id):
    """
    PHASE 8A — Step 5: safe, structured application-level error body.
    Never includes provider exception text, headers, or any secret --
    only a stable `code` the frontend already knows how to branch on
    (see templates/main.html's extractApiError()/classifyFailureState()),
    a human-safe `message`, whether retrying is expected to help, and the
    request_id for support/debugging. `request_id` may be None for
    failures that happen before one is known (e.g. missing config).
    """
    body = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "request_id": request_id,
        },
    }
    # Kept at the top level too (in addition to error.request_id) --
    # existing callers/tests read a top-level "request_id" on every other
    # /api/ai/ error response; this keeps that contract intact instead of
    # only adding it in the new nested shape.
    if request_id is not None:
        body["request_id"] = request_id
    return JsonResponse(body, status=status)


# PHASE 8A — Step 3/5: fine-grained classification of a Groq HTTP error
# response, keyed by the OpenAI-compatible `error.code` Groq returns (see
# https://console.groq.com/docs/errors). Falls back to a status-code-only
# bucket when the body doesn't carry a recognizable `code` (e.g. an
# upstream proxy/load-balancer error with no JSON body at all -- see
# _safe_error_body()). This is what actually distinguishes "the model ID
# this app is configured with was retired" (AI_CONFIGURATION_ERROR -- an
# operator problem, not solved by the user retrying) from a transient
# outage (AI_PROVIDER_UNAVAILABLE -- solved by retrying) instead of
# collapsing every 4xx/5xx into the same generic message, which is
# exactly what made the Phase 8A model-decommission bug invisible in the
# UI.
_GROQ_CONFIG_ERROR_CODES = {"model_decommissioned", "model_not_found", "model_not_active"}


def _classify_http_failure(status_code, body_error):
    error_code = None
    if isinstance(body_error, dict):
        error_code = body_error.get("code")

    if status_code in (401, 403) or error_code in ("invalid_api_key", "invalid_request_error_auth"):
        return "AI_AUTH_FAILED", "AI service authentication failed. Your credits were not charged.", False
    if status_code == 429:
        return (
            "AI_PROVIDER_RATE_LIMITED",
            "The AI service is receiving too many requests right now. Your credits were not charged.",
            True,
        )
    if status_code == 400 and error_code in _GROQ_CONFIG_ERROR_CODES:
        return (
            "AI_CONFIGURATION_ERROR",
            "The AI service is temporarily misconfigured. Your credits were not charged. "
            "Our team has been notified.",
            False,
        )
    if status_code in (502, 503, 504):
        return (
            "AI_PROVIDER_UNAVAILABLE",
            "The AI service is temporarily unavailable. Your credits were not charged.",
            True,
        )
    # Any other 4xx/5xx: still safe/generic, still refunded, just not one
    # of the more specific buckets above.
    return (
        "AI_PROVIDER_UNAVAILABLE",
        "The AI service is temporarily unavailable. Your credits were not charged.",
        True,
    )


def _call_provider(prompt: str):
    """
    Calls Groq with the existing retry-twice/short-timeout behavior.
    Returns (outcome, payload):
      outcome == "success"   -> payload is (result_text, usage_dict_or_None)
      outcome == "timeout"   -> payload is an error_code string
      outcome == "network"   -> payload is an error_code string
      outcome == "http"      -> payload is (status_code, body_error)
      outcome == "malformed" -> payload is a description string
    Never raises for expected failure modes -- only for genuinely
    unexpected exceptions, which the caller treats as PROVIDER_FAILURE.
    """
    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 1024,
    }

    last_error = ("network", "unknown_error")

    for _attempt in range(_PROVIDER_MAX_ATTEMPTS):
        try:
            res = requests.post(GROQ_URL, headers=headers, json=payload, timeout=_PROVIDER_TIMEOUT_SECONDS)
        except requests.exceptions.Timeout:
            last_error = ("timeout", "provider_timeout")
            continue
        except requests.exceptions.ConnectionError:
            last_error = ("network", "provider_connection_error")
            continue
        except requests.exceptions.RequestException as exc:
            last_error = ("network", f"provider_request_error:{type(exc).__name__}")
            continue

        if res.status_code >= 400:
            return "http", (res.status_code, _safe_error_body(res))

        try:
            data = res.json()
        except ValueError:
            return "malformed", "provider_response_not_json"

        if "error" in data:
            return "http", (res.status_code or 500, data["error"])

        try:
            result_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return "malformed", "provider_response_missing_content"

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        return "success", (result_text, usage)

    return last_error[0], last_error[1]


def _safe_error_body(res):
    try:
        data = res.json()
        return data.get("error", data)
    except ValueError:
        return res.text[:500]


def _build_image_url(prompt: str) -> str:
    """
    Server-side equivalent of the URL the browser used to build directly
    (templates/main.html's old generateImage(), pre-Phase-1). Kept as its
    own function so the encoding rule lives in exactly one place.
    """
    encoded_prompt = urllib.parse.quote(prompt, safe="")
    return (
        f"{_IMAGE_PROVIDER_BASE_URL}{encoded_prompt}"
        f"?width={_IMAGE_WIDTH}&height={_IMAGE_HEIGHT}&model={IMAGE_MODEL}"
    )


def _call_image_provider(prompt: str):
    """
    Calls the image provider server-side and verifies it actually succeeds
    before the request is allowed to finalize/charge -- mirrors
    _call_provider()'s outcome/payload contract so ai_generate()'s
    finalize/refund logic doesn't need a separate code path per provider:

      outcome == "success"   -> payload is (image_url, usage_dict_or_None)
                                 usage is always None here -- this provider
                                 exposes no token/usage figure at all, which
                                 is exactly why photo is billed as a flat
                                 cost (accounts/credit_config.py) rather
                                 than the token formula.
      outcome == "timeout"   -> payload is an error_code string
      outcome == "network"   -> payload is an error_code string
      outcome == "http"      -> payload is (status_code, body_error)

    Deliberately does NOT download the full image body -- stream=True and
    an immediate .close() after checking the status code confirms the
    provider is willing and able to serve this prompt without pulling
    megabytes of image data through the Django process for no reason (the
    browser fetches the actual image bytes directly from `image_url`
    afterwards, same as before this phase).
    """
    url = _build_image_url(prompt)
    last_error = ("network", "unknown_error")

    for _attempt in range(_IMAGE_PROVIDER_MAX_ATTEMPTS):
        try:
            res = requests.get(url, timeout=_IMAGE_PROVIDER_TIMEOUT_SECONDS, stream=True)
        except requests.exceptions.Timeout:
            last_error = ("timeout", "provider_timeout")
            continue
        except requests.exceptions.ConnectionError:
            last_error = ("network", "provider_connection_error")
            continue
        except requests.exceptions.RequestException as exc:
            last_error = ("network", f"provider_request_error:{type(exc).__name__}")
            continue

        try:
            if res.status_code >= 400:
                return "http", (res.status_code, _safe_error_body(res))
            return "success", (url, None)
        finally:
            res.close()

    return last_error[0], last_error[1]


def ai_generate(request):
    # ── HTTP method validation ──────────────────────────────────────────
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=405)

    # ── CSRF validation ─────────────────────────────────────────────────
    # Enforced by django.middleware.csrf.CsrfViewMiddleware (this view no
    # longer carries @csrf_exempt). A request without a valid X-CSRFToken
    # header (or csrfmiddlewaretoken field) never reaches this line -- the
    # middleware returns its own 403 first. See PHASE_1_CREDIT_ENGINE.md
    # "CSRF policy" for what this does and does not protect against.

    # ── Identity resolution ─────────────────────────────────────────────
    user, guest_key = guest_identity.identify_requester(request)

    # ── Request pre-parse (Phase 2) ───────────────────────────────────────
    # The entitlement decision below is per-feature, so `feature` has to be
    # known before it runs -- that means content-type/size/JSON parsing
    # (normally grouped under "request validation") happens here, ahead of
    # the entitlement check, ahead of rate limiting, per PHASE_2_
    # ENTITLEMENT_ENGINE.md's request lifecycle (Step 11): entitlement and
    # quota are decided before rate limiting and before any credit
    # reservation. Prompt-length and request_id validation stay below,
    # after rate limiting, unchanged from Phase 1 -- only the minimum
    # needed to know `feature` moved up.
    content_type = request.content_type or ""
    if content_type != ai_request_config.ALLOWED_CONTENT_TYPE:
        return JsonResponse({"error": "Content-Type must be application/json."}, status=400)

    if len(request.body) > ai_request_config.MAX_BODY_BYTES:
        return JsonResponse({"error": "Request body too large."}, status=413)

    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    feature, feature_error = _clean_feature(body.get("feature"))
    if feature_error is not None:
        return feature_error

    # PHASE 1: the GROQ_API_KEY requirement only applies to Groq-backed
    # (text) features. This check used to run unconditionally before
    # `feature` was even known, which meant Photo -- once it's registered
    # as a feature at all -- would incorrectly 500 in any environment
    # missing a Groq key it never needed. The image provider (see
    # IMAGE_PROVIDER_NAME below) takes no secret at all.
    if feature != "photo" and not settings.GROQ_API_KEY:
        return _provider_error_response(
            "AI_CONFIGURATION_ERROR",
            "The AI service is not configured. Your credits were not charged.",
            status=500,
            retryable=False,
            request_id=None,
        )

    # ── AI access policy (entitlement + quota) ──────────────────────────
    policy_response = _check_access_policy(user, guest_key, feature)
    if policy_response is not None:
        return policy_response

    # ── Rate limit ───────────────────────────────────────────────────────
    allowed, retry_after, _scope = rate_limit.check_ai_rate_limit(request, user=user, guest_key=guest_key)
    if not allowed:
        response = JsonResponse(
            {"error": "Too many requests. Please slow down and try again shortly."}, status=429
        )
        response["Retry-After"] = str(retry_after)
        return response

    # ── Request validation (remainder) ──────────────────────────────────
    prompt = (body.get("prompt") or "").strip()
    if len(prompt) > ai_request_config.MAX_PROMPT_CHARS:
        return JsonResponse(
            {"error": f"Prompt too long (max {ai_request_config.MAX_PROMPT_CHARS} characters)."}, status=400
        )

    # Idempotency key: use the caller's if given (recommended -- see
    # PHASE_1_CREDIT_ENGINE.md and templates/main.html's aiRequest()
    # helper, which always sends one now), otherwise generate one. A
    # server-generated id only protects THIS single call from being
    # double-processed; it does not make a naive client-side retry
    # idempotent, since a retry without a reused id looks like a brand-new
    # request.
    request_id, request_id_error = _clean_request_id(body.get("request_id"))
    if request_id_error is not None:
        return request_id_error

    # ── Idempotency ──────────────────────────────────────────────────────
    existing = ai_usage_service.check_existing_usage(request_id, user=user, guest_key=guest_key)
    if existing is not None:
        return _replay_existing(existing)

    # PHASE 1: which provider/model this request is billed and routed
    # against depends on `feature` -- photo uses the image provider, every
    # other feature keeps using Groq exactly as before.
    is_photo = feature == "photo"
    provider_name = IMAGE_PROVIDER_NAME if is_photo else PROVIDER_NAME
    provider_model = IMAGE_MODEL if is_photo else GROQ_MODEL

    # ── Credit reservation ───────────────────────────────────────────────
    try:
        ai_usage, account = ai_usage_service.begin_request(
            request_id=request_id,
            user=user,
            guest_key=guest_key,
            feature=feature,
            provider=provider_name,
            model=provider_model,
            prompt=prompt,
        )
    except ValidationFailure:
        return JsonResponse({"error": "Please enter what you want!", "request_id": request_id}, status=400)
    except InsufficientCreditsError:
        return JsonResponse(
            {"error": "Insufficient credits for this request.", "request_id": request_id}, status=402
        )
    except DuplicateRequestError:
        # PHASE 8A -- credit race-condition audit. Genuinely concurrent
        # duplicate submissions (same request_id from the same identity,
        # e.g. a double-click or a naive client-side retry that races
        # ahead of the first attempt's response) can both pass the
        # check_existing_usage() lookup above before either has
        # committed -- that lookup is a plain SELECT, not locked against
        # this race by design (locking it would serialize every distinct
        # request behind a shared lock for no benefit, since distinct
        # request_ids can't conflict). The loser then hits
        # credit_service.reserve()'s DB-level uniqueness constraint on
        # (account, reference_id, transaction_type) inside
        # begin_request() and raises DuplicateRequestError -- previously
        # uncaught here, producing an unhandled 500 for the losing
        # request instead of the same graceful idempotent-replay
        # response a slightly-later duplicate already gets via
        # check_existing_usage() above. The winner's AIUsage row is now
        # guaranteed to exist (it's what caused the constraint to fire),
        # so re-running the same lookup and handing off to the same
        # _replay_existing() every other duplicate path already uses
        # closes the race without inventing a second response shape.
        existing = ai_usage_service.check_existing_usage(request_id, user=user, guest_key=guest_key)
        if existing is not None:
            return _replay_existing(existing)
        # Defensive fallback -- should be unreachable (the constraint
        # only fires because a row with this reference_id now exists),
        # but never surface a raw 500 for a request the server itself
        # recognizes as a duplicate.
        return JsonResponse(
            {"error": "This request is already being processed.", "request_id": request_id}, status=409
        )

    # ── Provider ─────────────────────────────────────────────────────────
    started = time.monotonic()
    outcome, payload = _call_image_provider(prompt) if is_photo else _call_provider(prompt)
    latency_ms = int((time.monotonic() - started) * 1000)

    # ── Finalize / refund + usage ledger ────────────────────────────────
    if outcome == "success":
        result_text, usage = payload
        usage = usage or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        charged = ai_usage_service.complete_success(
            ai_usage,
            account,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            result_preview=result_text,
            latency_ms=latency_ms,
        )
        # Quota consumption (Step 16) only counts a request that actually
        # reached and succeeded with the provider -- mirrors the credit
        # engine's "never charge for a request the server refused/failed"
        # principle, applied to the entitlement layer's own counters.
        entitlement_service.record_usage(user, guest_key, feature)
        snapshot = _balance_snapshot(account)
        return JsonResponse(
            {
                "result": result_text,
                "request_id": request_id,
                "credits_charged": charged,
                "credits_available": snapshot["available_balance"],
            }
        )

    # Every other outcome reached (or tried to reach) the provider after a
    # reservation was made -- Step 11: always refund, never leave credits
    # permanently reserved.
    #
    # PHASE 8A — Step 5: each outcome now maps to one of the structured
    # AI_* error codes instead of a single generic "AI provider returned
    # an error." for every non-2xx response. `http` is classified further
    # by _classify_http_failure() since a 400 model_decommissioned, a 401
    # bad key, a 429 rate limit, and a 503 outage are different problems
    # with different (and differently actionable) user-facing messages.
    _HTTP_STATUS_BY_CODE = {
        "AI_AUTH_FAILED": 502,
        "AI_PROVIDER_RATE_LIMITED": 429,
        "AI_CONFIGURATION_ERROR": 502,
    }
    if outcome == "http":
        http_status_code, error_body = payload
        api_error_code, message, retryable = _classify_http_failure(http_status_code, error_body)
        status_name = "PROVIDER_FAILURE"
        body_code = error_body.get("code") if isinstance(error_body, dict) else None
        error_code = f"http_{http_status_code}:{body_code}" if body_code else f"http_{http_status_code}"
        # Default to 502 (Bad Gateway) for any upstream HTTP failure not
        # given a more specific status above -- matches this endpoint's
        # pre-Phase-8A behavior for every "http" outcome, so existing
        # callers/tests that expect a plain provider error to surface as
        # 502 are unaffected by the new finer-grained error `code`.
        http_status = _HTTP_STATUS_BY_CODE.get(api_error_code, 502)
    elif outcome == "timeout":
        api_error_code, message, retryable = (
            "AI_PROVIDER_TIMEOUT",
            "The AI service took too long to respond. Your credits were not charged.",
            True,
        )
        status_name, http_status = "TIMEOUT", 503
        error_code = payload if isinstance(payload, str) else outcome
    elif outcome == "malformed":
        api_error_code, message, retryable = (
            "AI_PROVIDER_INVALID_RESPONSE",
            "The AI service returned an unexpected response. Your credits were not charged.",
            True,
        )
        status_name, http_status = "MALFORMED_RESPONSE", 502
        error_code = payload if isinstance(payload, str) else outcome
    else:  # "network" or any unrecognized outcome
        api_error_code, message, retryable = (
            "AI_PROVIDER_UNAVAILABLE",
            "The AI service is temporarily unavailable. Your credits were not charged.",
            True,
        )
        status_name, http_status = "PROVIDER_FAILURE", 503
        error_code = payload if isinstance(payload, str) else outcome

    ai_usage_service.complete_failure(
        ai_usage,
        account,
        status=status_name,
        error_code=error_code,
        latency_ms=latency_ms,
    )

    return _provider_error_response(
        api_error_code, message, status=http_status, retryable=retryable, request_id=request_id
    )


def _replay_existing(existing):
    """Idempotent replay for a request_id already seen from this same identity."""
    request_id = existing.request_id

    if existing.status == existing.Status.FINALIZED:
        return JsonResponse(
            {
                "result": existing.metadata.get("result_preview", ""),
                "request_id": request_id,
                "credits_charged": existing.credits_charged,
                "replayed": True,
            }
        )
    if existing.status == existing.Status.VALIDATION_FAILURE:
        return JsonResponse({"error": "Please enter what you want!", "request_id": request_id}, status=400)
    if existing.status == existing.Status.INSUFFICIENT_CREDITS:
        return JsonResponse(
            {"error": "Insufficient credits for this request.", "request_id": request_id}, status=402
        )
    if existing.status == existing.Status.REFUNDED:
        return JsonResponse(
            {
                "error": "This request failed previously and was refunded. Please try again.",
                "request_id": request_id,
            },
            status=502,
        )
    # RESERVED / REQUESTED: a duplicate of an in-flight request.
    return JsonResponse(
        {"error": "This request is already being processed.", "request_id": request_id}, status=409
    )


def _balance_snapshot(account):
    from accounts.services import credit_service

    return credit_service.get_balance_snapshot(account)
