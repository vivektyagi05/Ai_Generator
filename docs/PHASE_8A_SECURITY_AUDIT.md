# PHASE 8A SECURITY AUDIT

Real execution paths were followed for each item below (not a keyword
grep alone, though grep was used to find candidates to then trace).
Items with a fix link to the relevant PHASE_8A_PROGRESS.md entry.

| Area | Finding | Status |
|---|---|---|
| Authentication bypass (generation) | Guest could reach the real provider and spend real credits | **Fixed — Item 2** |
| Authorization bypass (own-data isolation) | `history_view`/`delete_history`/`profile_data` all filter by `user=request.user`; `delete_history` additionally filters by `id=item_id, user=request.user` (an IDOR attempt against another user's history row is a silent no-op, not an error that would leak existence) | Already correct |
| Anonymous AI generation | Same as authentication bypass above | **Fixed — Item 2** |
| Credit spoofing | No client-supplied balance is ever trusted; every balance in a response is read back from the DB after the real mutation | Already correct |
| Entitlement spoofing | Decision is computed server-side per request from `user`/`guest_key`, never accepted from the client | Already correct |
| Direct API invocation (bypassing frontend) | `_check_access_policy()` runs identically regardless of caller — no frontend-only gate exists anywhere in the reservation/provider path | Already correct |
| CSRF | No `@csrf_exempt` anywhere in `accounts/views.py`; standard Django middleware protects every POST endpoint including all 4 forgot-password views | Already correct |
| GET mutations | Every mutating view is `@require_POST` or does an internal method check; read-only views (`plans_page`, `payment_status_view`, `entitlements_view`, etc.) correctly have no such gate | Already correct — audited fresh, Item 7 |
| XSS | Every AI-generated result rendered into `innerHTML` (all 5 generators' `onSuccess` handlers, `templates/main.html`) goes through `escapeHtml()` first | Already correct — checked fresh this phase |
| Generated AI HTML | Same as XSS — no raw provider output is ever inserted un-escaped | Already correct |
| History isolation | See Authorization bypass above | Already correct |
| Profile isolation | `profile_data`/`profile_update`/`profile_delete`/`profile_avatar` all operate on `request.user` only, all `@login_required` | Already correct |
| Password-reset enumeration | Send-step already correct; **verify-step side channel found and fixed — Item 4** | **Fixed — Item 4** |
| OTP brute force | `MAX_OTP_ATTEMPTS` lockout, cooldown + rolling send window, per-IP throttle | Already correct |
| Session fixation | `user_login()` reads guest identity before `login()`, which rotates the session key | Already correct — checked fresh this phase |
| Logout | POST-only form (both desktop and mobile-panel copies); no `<a>` anywhere points at `/logout/` — proven by `test_phase8_nav_logout_safety.py` across all 5 authenticated pages | Already correct |
| Open redirects | Login's `next` param validated via `url_has_allowed_host_and_scheme()` before use | Already correct — checked fresh this phase |
| Sensitive API responses | Spot-checked `/api/entitlements/`, `/credits/balance/`, `/profile/data/` response bodies — no password hash, no session key, no other user's data present | Already correct |
| Provider URL/key exposure | Groq/image-provider API keys are read server-side from environment (`settings.GROQ_API_KEY` etc.) and never appear in any template or static JS file (pattern search, zero matches) | Already correct — checked fresh this phase |
| Secrets in templates | Same search as above — zero hardcoded secrets found in any `.html`/`.js` file | Already correct — checked fresh this phase |
| Secrets in JavaScript | Same — only the Razorpay *public* `key_id` (server-issued per order, not a secret) reaches `static/js/billing.js` | Already correct — checked fresh this phase |
| Unsafe file uploads | `profile_avatar()`: size cap, Content-Type pre-filter, and a real Pillow decode-and-verify of file content (not metadata-only) | Already correct — checked fresh this phase |
| Payment-related endpoints | `create_order_view`/`verify_payment_view` are `@require_POST`; `payment_status_view` is read-only and correctly ungated; UI is genuinely wired to them (Item 9) — deeper Razorpay/webhook hardening is explicitly Phase 8B's scope, not re-audited beyond this | Already correct for what's in scope this phase |
| Race conditions around credits | Different-request_id race already covered by `test_credit_concurrency.py`; **same-request_id race found and fixed — Item 5** | **Fixed — Item 5** |

## Summary

Two real, previously-unfixed security-relevant bugs were found and
fixed this phase: the anonymous-generation credit/entitlement bypass
(Item 2) and the forgot-password verify-step enumeration side channel
(Item 4). One race-condition bug adjacent to the credit system was
found and fixed (Item 5). Every other item on this list was traced
fresh against the real code and found already correct — not
re-asserted from a prior phase's audit without checking.
