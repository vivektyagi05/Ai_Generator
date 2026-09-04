# PHASE 6 STEP 6.5 — Entitlement/Credit UX Audit

Status legend: CODE-VERIFIED · TEST-VERIFIED · NOT DONE.

## What existed before this batch

The credit pill (GAP-04, earlier session) already showed real balance,
refreshed after every generation. The **reactive** failure path already
had real entitlement UX at attempt-time: `renderAiFailure()` already
distinguished 401 (sign-in), 402/403 `PLAN_NOT_ALLOWED`/429
`QUOTA_EXCEEDED` (a real `BillingFlow`-driven exhaustion card with a
working Upgrade button), from generic errors. What was missing: nothing
told the user *before* they tried that a generator was unavailable to
them — the reactive-only design meant the first sign was always a failed
attempt.

## What this batch added

**A real bug caught during development, worth stating plainly:** my
first draft compared `/api/ai/`'s request slug (`"code"`, `"story"`) as
if it were also `/api/entitlements/`'s feature identifier. I called the
real endpoint before shipping this and found it returns the internal
`Feature.code` instead — `"AI_CODE"`, `"AI_STORY"` (CODE-VERIFIED against
`accounts/entitlement_config.py`'s `FEATURE_DEFINITIONS`, then
TEST-VERIFIED by actually calling `/api/entitlements/` in a test and
reading the real JSON, not by re-reading the source a second time and
trusting it more carefully). A slug-to-slug comparison would have
silently matched nothing — the banner would never have appeared under
any circumstance, and no test would have caught it, because "the banner
never showing" looks identical to "everything's allowed" from a test
that only checks for its absence. `GENERATOR_SLUG_TO_ENTITLEMENT_FEATURE_CODE`
fixes this, and `test_every_generator_slug_used_by_the_frontend_map_is_a_real_feature_definition`
locks the frontend map to the real source of truth so future drift in
`FEATURE_DEFINITIONS` gets caught by a test, not by the banner going
silent again.

**Proactive entitlement banner.** `refreshEntitlementBanner()` calls the
real `/api/entitlements/`, finds the currently-selected generator's entry
by the (now correct) feature code, and — only if the server says
`allowed: false` — shows a banner using human copy for whichever of the
seven real `DenialReason` codes the server returned
(`FEATURE_DISABLED`/`PLAN_NOT_ALLOWED`/`ENTITLEMENT_INACTIVE`/
`ENTITLEMENT_EXPIRED`/`QUOTA_EXCEEDED`/`GUEST_RESTRICTION`/
`AUTHENTICATION_REQUIRED` — CODE-VERIFIED against
`entitlement_service.DenialReason`, not invented). Refreshed on initial
load, on every generator switch (`switchTab()`), and after every
successful generation (quota changes as a result of a success, same
reasoning as the credit-pill refresh).

**One billing integration, not two.** For upgrade-eligible reasons, the
banner calls the exact same `renderExhaustionCard()` the reactive
failure path already used — extended with an optional `title` parameter
(default unchanged: `"Credits exhausted"`, so the original reactive call
site's behavior is untouched) rather than duplicating the `BillingFlow`
wiring a second time.

**Never authoritative.** The banner reads `featureEntry.allowed` — the
server's own boolean — and never computes or overrides it. It doesn't
disable the Generate button or block submission; `/api/ai/` remains the
sole real authority and re-checks entitlement/credits/rate-limit
unconditionally on every actual request, exactly as before this batch —
this UI is purely advisory, saving a wasted click when the answer is
already known.

TEST-VERIFIED: `accounts/tests/test_entitlement_ux.py`, 9/9 passing —
including the real-endpoint contract test that caught the slug/code bug
above, and an explicit assertion that no client-side
`isPremium`/`entitlement` flag is ever assigned.

Full suite after this batch: **501 tests, OK, skipped=9** (up from 492),
zero regressions.

## What this batch did NOT do

- **Plan name display.** The banner shows per-generator availability, but
  there's no persistent "You're on FREE / PREMIUM" label near the credit
  pill the way 6.5's brief described ("current plan" as a header
  element). `summary.plan` is available in the same response already
  being fetched — this is a small, real gap, not a hidden one.
- **`/api/plans/` (the catalog endpoint) is not called anywhere in
  `main.html`.** The Upgrade CTA goes straight to `BillingFlow.upgrade()`
  with a hardcoded `"PREMIUM"` plan code (same as the pre-existing
  reactive exhaustion card did) rather than letting the user pick from
  the real catalog inline — `/plans/` (the page) already exists
  separately for that.
- **Remaining state 6.5 named as "8. API/network failure" for the
  banner itself** is handled by hiding the banner (fails closed on
  *showing*, not on *authorizing* — the real authorization is
  unaffected either way since it never depended on this fetch
  succeeding), but there's no visible "couldn't check your access, try
  again" indicator distinguishing a network hiccup from "you're fully
  allowed." Worth flagging as a real, if minor, UX gap rather than
  silently treating "hidden" and "allowed" as equivalent outcomes for
  the person reading this audit.
