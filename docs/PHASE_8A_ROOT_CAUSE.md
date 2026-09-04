# PHASE 8A — Root Cause: Story / Application / Proposal / Code failing, Photo working

## Reported symptom

Manual verification of the running app (not just the test suite) found:

- Photo Generator: works
- Story Generator: fails — "AI provider returned an error. Retry"
- Application Generator: fails
- Proposal Generator: fails
- Code Generator: fails

## Root cause

`AI_GENERATORS/api_views.py` hardcoded `GROQ_MODEL = "llama-3.1-8b-instant"`.

Groq announced deprecation of `llama-3.1-8b-instant` (and `llama-3.3-70b-versatile`)
on **2026-06-17**, with a shutdown date of **2026-08-16**
(https://console.groq.com/docs/deprecations). After the shutdown date, every
request to that model ID returns:

```json
HTTP 400
{
  "error": {
    "message": "The model `llama-3.1-8b-instant` has been decommissioned and is no longer supported.",
    "type": "invalid_request_error",
    "code": "model_decommissioned"
  }
}
```

`_call_provider()` correctly caught this as an `"http"` outcome (refunding the
reservation — credit safety was never broken), but the response was mapped to
the single generic message `"AI provider returned an error."` for *every*
non-2xx Groq response, so a permanent configuration problem (wrong model ID)
was indistinguishable in the UI from a transient outage.

**Story, Application, Proposal, and Code all go through this exact same
`_call_provider()` / `GROQ_MODEL` path — that's why all four failed
identically and simultaneously.** Photo was never affected because it calls
a completely separate provider (Pollinations, via `_call_image_provider()`),
which has no model-deprecation relationship to Groq at all. Photo's success
was correctly *not* treated as evidence the text-generation path worked.

## Fix

1. `GROQ_MODEL` changed to `"openai/gpt-oss-20b"` — Groq's own recommended
   replacement for `llama-3.1-8b-instant` (same fast/small tier).
2. Provider HTTP failures are now classified into specific, safe error codes
   (`AI_AUTH_FAILED`, `AI_PROVIDER_RATE_LIMITED`, `AI_CONFIGURATION_ERROR`,
   `AI_PROVIDER_UNAVAILABLE`, `AI_PROVIDER_TIMEOUT`,
   `AI_PROVIDER_INVALID_RESPONSE`) instead of one generic message, so a
   future model deprecation (or any other config-level provider failure)
   surfaces as `AI_CONFIGURATION_ERROR` — visibly different from a transient
   `AI_PROVIDER_UNAVAILABLE` — instead of hiding behind a generic string.
3. `accounts/tests/test_phase8a_provider_error_classification.py` adds a
   direct regression test (`test_current_model_constant_is_not_the_
   decommissioned_one`) that fails the build outright if `GROQ_MODEL` is
   ever set back to a model Groq has published as shut down, plus one
   reproduction test per affected generator (Story/Application/Proposal/
   Code) using Groq's actual `model_decommissioned` response shape.

## What this fix does *not* cover

Model deprecation is an ongoing operational reality of using a third-party
model provider — Groq deprecates and retires models on a rolling schedule
(see their deprecations page). `openai/gpt-oss-20b` is Groq's current
production-tier recommendation as of this writing, but it will eventually
be deprecated too. Nothing in this codebase currently *detects* an
approaching or already-crossed deprecation date automatically (Groq does
not expose that via the chat-completions API — it only starts returning
`model_decommissioned` errors on/after the shutdown date). The safest
mitigation available without a live provider health check is:

- periodically re-checking `https://console.groq.com/docs/deprecations`
  against `GROQ_MODEL`, and
- watching for `AI_CONFIGURATION_ERROR` in production logs/error tracking,
  since that is now the code's own signal that this exact failure mode has
  recurred.

## Verification performed vs. not performed

- **Verified by code + full test suite in this environment:** the failure
  reproduction, the refund/credit-safety behavior, the error
  classification, and that no secret/provider-internal text leaks into the
  response. `manage.py check`, migration check, and the full Django test
  suite (805 tests) all pass.
- **Not verified here (and cannot be, in this sandbox):** an actual live
  HTTP call to `api.groq.com` with a real `GROQ_API_KEY`, and an actual
  live HTTP call to `image.pollinations.ai`. This sandbox's network egress
  is limited to package registries (pypi/npm/github/etc.) and does not
  include either provider's domain, and no real `GROQ_API_KEY` was
  available. **Before considering this phase closed, run one real request
  per generator (Story/Application/Proposal/Code/Photo) against the live
  app with a real `GROQ_API_KEY`** and confirm the response is a normal
  generated result, not a repeat of `model_decommissioned` or any other
  error. If it fails again, the response body's `error.code` will now say
  exactly why (e.g. `AI_AUTH_FAILED` means the key itself is the problem,
  not the model).
