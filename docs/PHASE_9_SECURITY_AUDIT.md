# PHASE 9 — Security Audit

Scope: Steps 1–3, 6, 8 of PHASE_9_PROGRESS.md, re-summarized here as the
canonical security-focused record. Every finding below was verified
against actual running code/tests, not against prior documentation.

## Summary table

| Area | Finding | Status |
|---|---|---|
| `DEBUG`/`SECRET_KEY`/`ALLOWED_HOSTS` | Fail-closed, correctly configured | Verified, no change |
| Cookies (`HttpOnly`/`SameSite`/`Secure`) | Already correct in non-DEBUG | Verified, no change |
| `SECURE_SSL_REDIRECT` / HSTS | Not safe to force on blindly (proxy-topology dependent) | **Fixed** — opt-in `BEHIND_TLS_PROXY` flag, correctly wires `SECURE_PROXY_SSL_HEADER` |
| Rate limiting backend | `LocMemCache` unsafe across multiple workers | **Fixed** — `REDIS_URL`-driven shared cache, Django's built-in Redis backend |
| Login brute force | **No throttling existed at all** | **Fixed** — per-IP + per-email caps, checked before `authenticate()` |
| Account deletion → media | Avatar file orphaned in storage | **Fixed** — explicit `.delete(save=False)` |
| Account deletion → financial data | CASCADE-deleted `Payment`/`Subscription`/`CreditTransaction`/`AIUsage` | **Fixed** — anonymize+deactivate instead of hard delete (see Step 7 in PROGRESS.md) |
| Account deletion → sessions | Only the current session was flushed | **Fixed** — every session for the user is now revoked |
| Avatar upload → decompression bomb | `DecompressionBombError` uncaught → 500 | **Fixed** — added to caught exception tuple |
| Avatar upload → content validation | Already Pillow-decodes and checks real format, not just extension | Verified, no change |
| Razorpay webhook | Signature-first verification, no CSRF cookie dependency, idempotent event handling | Verified, no change |
| CSRF | `@require_POST` present on all mutating views checked; no GET-based mutation bypass found | Verified, no change |
| Secrets in logs | Grepped for `logger.*` calls near password/OTP/secret/token identifiers — only setting *names* logged, never values | Verified, no change |
| SQL injection | No raw SQL/string-interpolated queries found — ORM used throughout | Verified, no change |
| IDOR | Every mutating view checked scopes queries by `request.user`/`request.session` — spot-checked `delete_history`, `profile_*`, subscription cancel/restore | Verified, no change |

## `manage.py check --deploy`

Before this pass: 2 warnings (`security.W004`, `security.W008`) — both
about `SECURE_SSL_REDIRECT`/HSTS, both explained above.

After this pass: **0 warnings** when `BEHIND_TLS_PROXY=True` is set (the
correct value once the deployment's proxy setup is confirmed); unchanged
(still the same 2, informational) if left unset, which is the safe
default until that confirmation happens.

## Known residual risk (not fixed — explicitly out of scope for this pass)

- **CSP (Content-Security-Policy)** was not added. The brief said
  "where compatible" — this app currently has inline `<script>` blocks
  throughout its templates (see PHASE_9_PROGRESS.md's frontend section),
  which would require either a nonce-based CSP (real work: threading a
  per-request nonce through every template) or `unsafe-inline` (which
  defeats most of CSP's XSS value). Adding a weak CSP for the sake of
  having one is worse than not having one — flagging as a real future
  improvement rather than doing it superficially here.
