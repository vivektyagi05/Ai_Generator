# PHASE 7 SECURITY AUDIT

Scope: (1) confirm what Phase 6 already covered, by reading the actual
test files, not assuming; (2) audit every surface this session actually
added or changed; (3) fix concrete findings, not hypothetical ones.

## Phase 6 coverage confirmed (read, not re-implemented)

| Area | Test file | Real/behavioral? |
|---|---|---|
| CSRF on `/api/ai/` | `test_ai_csrf.py` | Yes -- `enforce_csrf_checks=True` client |
| XSS in generated content | `test_security_xss.py` | Yes -- asserts `escapeHtml()` present in the exact `innerHTML=` assignment for all 4 text/code generators |
| XSS in history (query + response) | `test_history_workspace.py` | Yes -- actual injected `<script>`/`<img onerror>` payloads |
| Entitlement/credit spoofing | `test_credit_security.py`, `test_entitlement_security.py`, `test_ai_credit_integration.py`, `test_ai_entitlement_integration.py` | Yes -- cross-user isolation, field-spoofing attempts |
| Provider credential exposure | `test_ai_photo_integration.py` and others (`GROQ_API_KEY`/provider URL never appear in any response body) | Yes |

None of the above were re-implemented or duplicated this session --
confirmed present and passing, left alone.

## New surfaces introduced this session -- audited

### `GET /api/ai/costs/` (new endpoint)

- **Input surface**: none. Takes no query params, no body. Verified a
  request with `?credits=0&cost=1&feature=photo&minimum_credits=0`
  returns byte-identical output to a plain request
  (`test_phase7_security_sweep.py::test_get_request_ignores_arbitrary_query_params`).
- **Credential leakage**: response body never contains `GROQ`,
  `api_key`, `API_KEY`, `Bearer `, or `sk-`
  (`test_response_never_contains_provider_credential_markers`).
- **Cannot influence a real charge**: it's read-only, computed
  independently from the real reservation path
  (`accounts/services/ai_usage_service.py` calls the same
  `estimate_reservation_credits()` itself, not through this endpoint) --
  calling it repeatedly doesn't change its own answer, confirmed by
  test.
- **Method**: GET-only, POST returns 405 (Django's CSRF exemption for
  safe methods applies correctly; no unsafe method exists on this view).

### Copy actions (Story/Application/Proposal)

- Read already-rendered, already-escaped DOM text (`.innerText` of the
  existing output element) and write it to the clipboard via the SAME
  `copyTextToClipboard()` helper Code's copy button already used --
  introduces no new `innerHTML` sink and no new data source, so it
  carries no XSS surface beyond what `test_security_xss.py` already
  covers for those same output elements.

### Rate-limit `Retry-After` display

- Reads `response.headers.get("Retry-After")` from the real fetch
  response object -- never fabricated. When absent/unparseable, the
  wait-time line is omitted rather than showing an invented number
  (`test_phase7_rate_limit_ux.py::test_render_rate_limited_never_fabricates_a_wait_time`).

### Result-heading / workspace-shell / subtitle / status-badge additions

- All render via `.textContent` assignment or static template markup --
  no new `.innerHTML` sink introduced anywhere in this batch of changes.
  Verified by grep during each batch (no `innerHTML` in
  `renderCostHint`/`renderRateLimited`/`setGeneratorStatusBadge`).

## Findings fixed this session

None of the findings in this session were security vulnerabilities in
the traditional sense (no XSS, no CSRF gap, no auth bypass was
introduced or found). The one relevant fix was **information gap, not
exposure**: the backend already sent a real `Retry-After` header that
the frontend silently discarded -- read and surfaced honestly, adding no
new attack surface.

## Explicit checklist (7.15/7.16 items)

- [x] XSS in every generated result -- confirmed pre-existing, correct.
- [x] XSS in history -- confirmed pre-existing, correct.
- [x] CSRF -- confirmed pre-existing, correct; new `/api/ai/costs/` is
      GET-only so CSRF doesn't apply.
- [x] Provider URL/key leakage -- confirmed absent from all responses
      including the new endpoint.
- [x] Entitlement spoofing -- confirmed pre-existing coverage, not
      touched.
- [x] Credit/cost spoofing -- confirmed pre-existing coverage; new
      endpoint has no spoofable input.
- [x] Unsafe query/deep-link values -- pre-existing
      `test_generator_deep_linking.py` coverage, not touched.
- [x] Unsafe DOM insertion -- confirmed no new `innerHTML` sinks added.
- [x] Duplicate requests -- pre-existing `REQUESTING`-state guard in
      `runGeneratorLifecycle()`, not touched.
- [x] Unauthorized history access -- pre-existing coverage, not touched.
- [x] Guest/authentication boundaries -- pre-existing coverage, not
      touched.

## New security-relevant tests added this session

`accounts/tests/test_phase7_security_sweep.py` (4 tests): no spoofable
input on the new endpoint, no credential leakage, repeatable/stable
output, GET-with-body tolerance.

## UPDATE (later session): 2 real findings, both fixed and tested

A subsequent session's shell-migration work on `profile.html`/
`history.html`/`plans.html` (see `PHASE_7_FRONTEND_COMPLETION_PROGRESS.md`
for the full account) surfaced 2 real security bugs this document's
"none of the findings... were security vulnerabilities" conclusion did
not anticipate, because they predated that session and were outside its
scope at the time:

1. **Self-XSS in `profile.html`'s activity feed** — `ChatHistory.query`
   (the user's own free-form generation prompt) was interpolated into
   `innerHTML` with zero escaping. A user's own stored prompt containing
   markup would execute when their own profile page rendered it back.
   Fixed with a new shared `DS.util.escapeHtml` in `static/js/api.js`
   (loaded on every page, before any page's own script runs), applied
   at the render site. `main.html`'s previously-separate local
   `escapeHtml` (which this document's original coverage table above
   references) now delegates to the same shared implementation, closing
   a real gap in that copy too: the local version didn't escape quotes;
   the shared one does.

2. **CSRF-bypass-via-GET on `delete_history`/`clear_history`** — both
   views had NO method restriction, reachable via a plain GET. Django's
   `CsrfViewMiddleware` only checks unsafe-by-convention methods
   (POST/PUT/PATCH/DELETE); GET is exempt by design. Confirmed
   exploitable pre-fix with a bare `client.get(...)` from an
   authenticated test session, which deleted the row with no CSRF token
   at all — meaning, pre-fix, a third-party page could delete a
   logged-in visitor's history with nothing more than
   `<img src="https://this-app.com/history/delete/1/">`. Fixed with
   `@require_POST` on both views. A full codebase sweep (programmatic:
   every view with `.save()`/`.delete()`/`.create()`/`set_password`/
   `update_session_auth_hash` checked for a `require_POST`-family
   decorator or a manual `request.method` check) found no other
   instances of this pattern.

Both are covered by dedicated regression tests
(`test_phase7_profile_xss_fix.py`, `test_phase7_history_csrf_fix.py`),
including tests confirming the legitimate POST/rendering paths still
work unchanged. This updates, rather than replaces, the checklist
above — the pre-existing Phase 6 coverage it confirmed is still
accurate; these 2 items are additions the original session's scope
didn't cover.
