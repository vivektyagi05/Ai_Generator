# PHASE 8A — ARCHITECTURE RECOVERY — PROGRESS CHECKPOINT

Single resumable checkpoint for Phase 8A. Do not create a new
per-batch document — update this one.

## STATE: IN PROGRESS

## Full regression status (last run this checkpoint)
- `python manage.py check` — OK (0 issues)
- `python manage.py makemigrations --check --dry-run` — OK (no changes detected)
- `python manage.py test accounts` — **785 tests, OK, 9 skipped** (skips pre-date this checkpoint, unrelated to items below)

---

## ITEM 1 — Duplicate navigation bug — **FIXED, VERIFIED**

**Root cause (proven, not assumed):** template inheritance was already
correct (every app-shell page extends `base_app.html` exactly once,
which includes `partials/nav_app.html` exactly once — no double
`{% extends %}` / double `{% include %}` anywhere). The bug was in
`static/css/shell.css`: `.ds-nav-mobile-panel` (the mobile drawer inside
`nav_app.html` — a plain `<a>` list: Generators/Dashboard/History/
Pricing/Login/Sign Up) only had `display: none` **inside**
`@media (max-width: 768px) { ... }`. Outside that breakpoint (any
desktop viewport) nothing hid it, so the browser's UA default
`display: block` for a `<div>` applied and it rendered as a second,
plain-link row directly under the real `<nav>` on every app-shell page,
for every desktop visitor. `nav_public.html` never had this element at
all (confirmed by grep), so the bug was scoped to authenticated/app-shell
pages only.

**Fix:** added an unconditional base-level `.ds-nav-mobile-panel {
display: none; }` rule in `static/css/shell.css`, ahead of the media
query (which still handles `.is-open` reveal + the responsive
override). One-line, root-cause fix — not a `display:none` patch
bolted onto broken markup; it restores the "closed by default" state
the component's own `@media` override already assumed existed.

**Regression tests added:** `accounts/tests/test_phase8_nav_duplicate_panel_fix.py`
- `MobilePanelBaseStateTests` — asserts the unconditional base rule
  exists (would have caught this before it shipped) and that the
  media-query reopen rule (`.is-open`) is intact.
- `AppShellRendersExactlyOneNavExperienceTests` — real `Client()`
  requests against `/home/`, `/dashboard/`, `/history/`, `/profile/`,
  `/plans/` (anonymous + authenticated), asserting exactly one `<nav>`
  element per page.

Also re-ran and confirmed passing: `test_phase7_responsive_verification.py`
(`SharedNavResponsiveTests`, which covers the inside-the-media-query
behavior this gap sat next to) and `test_phase8_nav_logout_safety.py`
(logout-safety of nav controls, unaffected but re-verified as part of
"lock the nav fix with evidence").

**Files changed:** `static/css/shell.css`,
`accounts/tests/test_phase8_nav_duplicate_panel_fix.py` (new).

---

## ITEM 2 — Anonymous 0 → 45 credit bug — **FIXED, VERIFIED**

### Reproduction / trace (answers to the lettered questions, from code, not inferred)

- **A. Does an anonymous request receive a guest CreditAccount?**
  Yes (prior to fix) — lazily, on first `credit_service.get_or_create_account(guest_key=...)`
  call, which only happened once a generation request passed the
  entitlement check.
- **B. Is `INITIAL_FREE_CREDITS` automatically granted to that guest?**
  Yes — `get_or_create_account()` grants `credit_config.INITIAL_FREE_CREDITS`
  (50) unconditionally on first creation of any account, guest or user.
- **C. Was Photo (or any feature) allowed for guests?**
  Yes — `entitlement_service._decide()`'s guest branch returned
  `allowed=True` for any feature as long as a per-feature daily quota
  (`GUEST_DAILY_LIMIT`, default 30) wasn't exceeded. No per-feature
  "guest allowed" flag exists on the `Feature` model — the same branch
  applied uniformly to all 5 generators.
- **D. Did the backend actually invoke the AI provider for an anonymous user?**
  Yes, confirmed via `AI_GENERATORS/api_views.py:ai_generate()` — once
  `_check_access_policy()` returned `None` (allowed), execution reached
  `ai_usage_service.begin_request()` (credit reservation) and then the
  real provider call (`_call_provider()` / `_call_image_provider()`).
- **E. Which exact account loses credits?** The guest-scoped
  `CreditAccount` (keyed on `guest_session_key`, a Django session key)
  — never any authenticated user's account.
- **F. Why did the page initially display 0?** Correctly, not a bug —
  `credit_balance()` (the endpoint the credit pill polls on load)
  deliberately does **not** create a guest account just to answer a
  balance query (see its own code comment: "no reason to hand out a
  free grant just for checking a balance that doesn't exist yet"). No
  account exists yet at that point, so 0 is accurate.
- **G. Why did the post-generation refresh reveal 45?** Also accurate,
  not fabricated — the guest's first generation call created the
  account (grant of 50), then reserved/charged the request's real cost
  (5), leaving an available balance of 45, a real ledger entry.
- **H. Same exposure for Story/Application/Proposal/Code?** Yes —
  confirmed architecturally (single shared guest branch, no per-feature
  carve-out) and by the fix's own test coverage across features.
- **I/J/K. Guest → login/signup → does guest balance become
  authenticated balance?** Yes, already — `credit_service.migrate_guest_credits_to_user()`
  (built in an earlier phase, tests in
  `test_phase8_guest_credit_migration.py`) moves any leftover guest
  balance into the new user's account on login/signup, idempotently,
  without a double initial grant. This machinery is left in place
  (not deleted) for any pre-existing guest balances from before this
  fix shipped, and as it's orthogonal to whether new guest balances can
  be created going forward.
- **L. Can authenticated balance resolve back to guest balance?** No —
  confirmed no code path does this; migration is one-directional.

### Verdict
This was real, working-as-implemented behavior from an earlier phase
(guest daily-quota-gated generation), not a latent/accidental bug — but
it is exactly what produced the reported "0 → 45" symptom, and the
product owner has now set a new canonical rule that supersedes it:
**an anonymous visitor must never reach the provider, the credit
ledger, or a CreditAccount at all.**

### Fix
`accounts/services/entitlement_service.py`'s `_decide()` guest branch
now returns `AccessDecision(allowed=False, reason=DenialReason.AUTHENTICATION_REQUIRED, ...)`
unconditionally, before any quota is computed. `DenialReason.AUTHENTICATION_REQUIRED`
already existed with full frontend wiring (copy in `templates/main.html`
and `templates/profile.html`, `401` status mapping in
`AI_GENERATORS/api_views.py`) and its own passing tests
(`test_ai_credit_exhaustion_ui.py`, `test_entitlement_ux.py`) — it was a
half-wired feature, never actually returned by the decision function,
not new plumbing.

**Effect verified end-to-end:** for a guest, `_check_access_policy()`
now short-circuits with `401 AUTHENTICATION_REQUIRED` before rate
limiting, before request_id/prompt parsing, and — critically — before
`ai_usage_service.begin_request()` (and therefore
`credit_service.get_or_create_account()`) is ever called. No
`CreditAccount` row, no credit grant, no reservation, no provider call,
no `AIUsage` row, no `ChatHistory` row is created for an anonymous
request to any of the 5 generators. Guest **session** identity
(`accounts/services/guest_identity.py`) is untouched — it's still used
for CSRF/session plumbing and by `migrate_guest_credits_to_user()` for
legacy balances; only AI-generation *authorization* was gated.

### Tests: old rule retired, new rule asserted (documented per the "never silently weaken" rule)

| File | Old assertion (retired) | New assertion |
|---|---|---|
| `test_entitlement_engine.py` | `test_guest_feature_access_allowed_under_daily_limit`: guest allowed under quota; `test_guest_restricted_once_daily_limit_reached`: denied only past quota, reason `GUEST_RESTRICTION` | `test_guest_is_denied_regardless_of_daily_limit`, `test_guest_denied_even_with_zero_prior_usage`: denied unconditionally, reason `AUTHENTICATION_REQUIRED`, independent of usage count |
| `test_ai_entitlement_integration.py` | `test_guest_within_limit_succeeds`: 200 on first guest request; `test_guest_over_daily_limit_blocked_before_provider_call`: 403 `GUEST_RESTRICTION` only after quota filled | `test_guest_first_request_is_denied_before_provider_call`: 401 on the very first request, provider never called; `test_guest_denial_is_independent_of_guest_daily_limit_value`: still denied even with `GUEST_DAILY_LIMIT` patched to 1000 |
| `test_ai_credit_integration.py` | `test_guest_can_use_free_initial_grant`: 200, real `AIUsage` row with no user | `test_guest_cannot_reach_provider_or_ledger`: 401, provider never called, no `AIUsage` row, **zero** `CreditAccount` rows created at all |
| `test_ai_rate_limit.py` (`GuestRateLimitTests`) | `test_guest_eventually_gets_429` / `test_guest_ip_cap_bounds_session_cycling`: guest rate limiter (8/hr session, IP cap) eventually returns 429 after N real requests | `test_guest_never_reaches_429_because_denied_earlier_every_time`, `test_guest_session_cycling_still_never_reaches_the_provider`: guest is denied 401 on every attempt (rate limiter is now unreachable dead-code from this endpoint specifically — left in place, not deleted, since it may still matter for other guest-accessible surfaces or a future reversal); provider never called across many attempts either way |
| `test_ai_request_validation.py` | Whole class ran unauthenticated, relying on reaching payload-validation code as a guest | `setUp()` now logs in a user — this file tests payload shape/size validation, not guest policy (that's `test_ai_entitlement_integration.py`'s job), and auth is checked before payload shape per the canonical gate order |
| `test_ai_csrf.py` | `test_valid_csrf_token_is_accepted` asserted 200 as a guest | Now logs in a user so CSRF acceptance (not guest policy) is what's actually being observed |

Every retired assertion encoded the pre-existing product decision to
let guests silently consume real credits — a decision the product
owner has explicitly reversed, not a test bug. No assertion was
weakened; each was replaced with one that proves the new, stricter
boundary.

**Files changed:** `accounts/services/entitlement_service.py`;
tests: `test_entitlement_engine.py`, `test_ai_entitlement_integration.py`,
`test_ai_credit_integration.py`, `test_ai_rate_limit.py`,
`test_ai_request_validation.py`, `test_ai_csrf.py`.

---

## ITEM 3 — Story/Application/Proposal/Code generators + Photo re-verification — **NO DIVERGENCE FOUND (already correct)**

Traced every one of the 4 reported-broken generators against the full
contract (button → handler → state object → `/api/ai/` payload →
feature slug → shared `runGeneratorLifecycle()`/`aiRequest()` controller
→ backend entitlement/credit/provider → response schema → renderer →
history save):

- All 4 buttons (`generateCode/generateStory/generateApplication/generateProposal`)
  are declared exactly once at module scope, each wired via a matching
  `onclick` on its own Generate button — no duplicates, no orphaned/
  dead handlers (grep-verified).
- All 4 use the correct canonical feature slugs
  (`code/story/application/proposal`), an exact match against
  `accounts/ai_request_config.py`'s `ALLOWED_FEATURES` and
  `templates/main.html`'s own `TAB_ID_TO_GENERATOR_PARAM` reverse map.
- All referenced DOM element ids (e.g. `storyTopic`, `storyCharacters`,
  `storyOutput`, `storyCostHint`) exist exactly once each in the
  rendered template — no id collisions, no missing elements.
- `runGeneratorLifecycle()` (the one shared engine all 5 generators run
  through) was read in full: correct state-machine gating (no
  duplicate-click races), stale-response guarding after reset, credit
  pill + entitlement banner refresh on success, history save — no bug
  found here that would explain "not working" for a subset of
  generators specifically.
- **Empirically re-verified, not just read**: a real `Client()` POST
  (authenticated) to the real `/api/ai/` view, provider HTTP mocked,
  for all 4 features — every one returns `200` with the exact response
  shape the frontend consumes (`result`/`credits_charged`/
  `credits_available`), and credits deduct correctly and sequentially
  from one shared account across generators (not per-generator silos,
  not a fresh grant each time). New test:
  `accounts/tests/test_phase8a_generator_reverification.py`.
- Photo re-verified via the existing `test_ai_photo_integration.py`
  (unaffected by Items 1/2, still fully green) — not duplicated in the
  new file since it already covers Photo's image-provider-specific path.

**Conclusion:** these 4 generators are not currently broken at the code
level in this repository snapshot. The most likely explanation for the
original report is that it was either (a) a symptom of the Item 2
credit/entitlement bug specifically as experienced by a guest session
(confusing/inconsistent state around the 0→45 flicker could plausibly
read as "generation isn't working" even when the request nominally
succeeded), or (b) an environment-specific issue (missing
`GROQ_API_KEY`, stale static files) rather than an application-code
defect — neither reproducible from the source alone. Flagging this
explicitly rather than silently closing it: if the report persists
after Items 1–2 ship, the next step would be checking deployment
environment config, not application code.

**Files changed:** `accounts/tests/test_phase8a_generator_reverification.py` (new).

---

## ITEM 4 — Forgot-password / OTP flow — **AUDITED; ONE REAL GAP FOUND + FIXED**

Traced the full flow: `forgot_send_otp` → `forgot_resend_otp` →
`forgot_verify_otp` → `forgot_reset_password` (`accounts/views.py`),
backed by the shared `accounts/otp_service.py` (`issue_otp`/`verify_otp`,
also used identically by the signup flow) and `accounts/rate_limit.py`
(cooldown/window/IP throttle).

**Already correct (verified, not assumed) — no changes needed:**
- No OTP is ever issued for a nonexistent account: `forgot_send_otp`
  checks `User.objects.filter(email=email).exists()` and returns the
  generic response *before* calling `issue_otp()` if it doesn't.
- `forgot_send_otp`/`forgot_resend_otp` return byte-identical generic
  JSON (`_FORGOT_SEND_GENERIC_RESPONSE`) for a registered vs.
  unregistered email, including on cooldown-throttled and email-provider-
  failure paths (already tested:
  `test_enumeration_resistant_response_bodies_match`,
  `test_brevo_failure_on_send_does_not_leak_via_enumeration_channel`).
- No dummy/blank OTP acceptance: `verify_otp()` requires a real,
  persisted `EmailOTP` row and a `hmac.compare_digest` match.
- Expiry, single-use (record deleted on success), max-attempts lockout
  (record deleted, forcing a fresh `issue_otp()`), send cooldown +
  rolling window, per-IP throttle: all present, all separately unit-
  tested (`test_otp_service.py`, `test_forgot_password.py`).
- CSRF: no `@csrf_exempt` anywhere in `accounts/views.py` — standard
  Django CSRF middleware protects every POST endpoint including all 4
  forgot-password views.
- Session binding: `reset_email`/`otp_verified` are server-side session
  keys, never client-supplied; `forgot_reset_password` fails closed
  (session flush, generic error) if `otp_verified` is set but the user
  row is somehow gone; session is flushed after a successful reset
  (already tested: `test_session_cleared_after_successful_reset`); a
  replayed reset-password submit after session flush already correctly
  403s (already tested:
  `test_replaying_reset_after_completion_fails_cleanly_not_silently`).

**Real gap found (not in the original bug report, found via this
item's own security-audit pass) — a verify-step enumeration side
channel:** `otp_service.verify_otp()`'s `EmailOTP.DoesNotExist` branch
returned a message ("Verification code not found or already used...")
distinguishable from the wrong-code-on-a-real-OTP branch ("Invalid
verification code."). Since `forgot_send_otp` never creates an
`EmailOTP` row for an unregistered email, an attacker could:
send-otp for a candidate email (generic response either way) → 
immediately verify-otp with any guess → read which message came back
to learn whether the account exists, without ever needing the real
code or waiting for a throttle window. This bypassed the send-step's
enumeration resistance entirely via a different endpoint.

**Fix:** `otp_service.py`'s `DoesNotExist` branch now returns the
identical message/status as the wrong-code branch
(`"Invalid verification code.", 400`). `expired`/`max-attempts`
messages are deliberately left distinct — both require a record to
have actually existed and are weaker, harder-to-weaponize signals for
a user who already knows they received a real code (resend vs.
retype), not the same immediately-exploitable oracle as this one.
Shared service function, so the same fix applies identically to the
signup OTP flow (`verify_otp_record` alias) — one fix, not two.

**Regression test added:** `test_forgot_password.py::test_verify_step_gives_identical_response_for_unregistered_email_and_wrong_code`
— proves a guessed code against a never-issued (unregistered-email)
OTP and a wrong guess against a real, pending OTP now return byte-
identical status + body.

**Files changed:** `accounts/otp_service.py`,
`accounts/tests/test_forgot_password.py`.

---

## ITEM 5 — Credit/entitlement business-rule audit (race conditions, refund/retry) — **ONE REAL BUG FOUND + FIXED**

Read `credit_service.py`'s `reserve()`/`finalize()`/`refund()`/`grant()` in
full: all correctly use `select_for_update()` + the DB-level
`uq_credittxn_account_reference_type` unique constraint, translating a
concurrent duplicate into `DuplicateRequestError` rather than allowing a
double-reservation or double-charge. `test_credit_concurrency.py`
already proves (Postgres-only, since SQLite doesn't enforce row locks —
correctly gated) that two *different* concurrent reservations can't
together exceed balance.

**Real gap found (empirically reproduced, not assumed):** that
constraint-violation translation was correct at the `credit_service`
layer, but nothing caught `DuplicateRequestError` further up the stack.
Specifically: `ai_usage_service.begin_request()`'s `credit_service.reserve()`
call can raise it — for the *same* `request_id`, not different ones —
when two genuinely concurrent submissions (double-click, or a naive
client-side retry racing the first attempt's response) both pass
`check_existing_usage()`'s unlocked `SELECT` before either has
committed its reservation. `AI_GENERATORS/api_views.py:ai_generate()`
only caught `ValidationFailure`/`InsufficientCreditsError` around that
call — `DuplicateRequestError` propagated uncaught, producing an
**unhandled 500** for the losing request, instead of the graceful `409
"already being processed"` a slightly-later duplicate already gets via
`_replay_existing()`. Reproduced directly at the service layer first
(`begin_request()` called twice with the same `request_id` → confirmed
`DuplicateRequestError` propagates), then at the HTTP layer via a test
that forces the exact TOCTOU window (patches `check_existing_usage` to
return `None` once while a real committed reservation already exists
for that `request_id`) and confirmed a raw, uncaught path before the fix.

**Fix:** `ai_generate()` now catches `DuplicateRequestError`, re-runs
`check_existing_usage()` (guaranteed to now find the winner's committed
row, since the constraint only fires because that row exists), and
hands off to the existing `_replay_existing()` — the exact same
idempotent-replay path every other duplicate-`request_id` case already
uses. No new response shape, no new file — reuses existing machinery
per the reuse-first rule. A defensive fallback (generic 409) covers the
theoretically-unreachable case where the row still isn't found.

**Why `finalize()`/`refund()` don't have the same exposure:** both are
only ever called by whichever single caller won the `reserve()` race for
a given `request_id` (the loser never gets past reservation to reach
them), so there's no analogous two-callers-for-one-`request_id` window
there.

**Regression test added:** `test_ai_idempotency_conflict.py::GenuinelyConcurrentSameRequestIdTests`
(extends the existing file that already owned this responsibility,
rather than a new file) — proves the losing concurrent request now gets
`409` with the standard `_replay_existing()` body, provider is never
called for it, and reserved balance reflects exactly one reservation,
not two.

**Files changed:** `AI_GENERATORS/api_views.py`,
`accounts/tests/test_ai_idempotency_conflict.py`.

---

## ITEM 6 — Dummy/unwired UI controls sweep — **ONE REAL INSTANCE FOUND + FIXED; SWEEP SCOPE WIDENED**

The existing `test_no_dead_ui.py` (`NoDeadOnclickHandlersRemainTests`)
only ever fetched `templates/main.html` — its own TODO/dead-link/dead-
onclick checks never covered `dashboard.html`, `history.html`,
`plans.html`, `profile.html`, `login.html`, `signup.html`,
`forget.html`, `verify_otp.html`, or the nav partials at all. Re-swept
directly (grep, not re-trusting the old test's coverage) across every
one of those for: `TODO`/`FIXME`/"Coming soon" markers, `href="#"` dead
links, and `<button>` elements with no `onclick`, no `type="submit"`,
no `id`, and no `data-*` JS hook (this codebase's established pattern
for JS wiring — `nav_app.html`'s `[data-ds-nav-toggle]`,
`plans.html`'s `[data-upgrade-btn]`/`[data-interval-toggle]`,
`history.html`'s `[data-filter]`).

**Real gap found:** `signup.html`'s Terms of Service / Privacy Policy
checkbox label was two literal `href="#"` anchors — confirmed (via
search across `AI_GENERATORS/urls.py` and `accounts/urls.py`) that no
Terms/Privacy route or view exists anywhere in the project, so these
went nowhere for every visitor.

**Fix:** per Phase 8A's dummy-control rule ("remove it or genuinely
connect it") — and since no legal-page infrastructure exists yet to
connect these to — replaced the two dead anchors with plain text.
Documented in-template as re-linkify-when-real-pages-exist, not a
silent removal.

**Every other candidate the sweep surfaced was verified to be real, not
dummy** (each checked individually against its template's own JS, not
assumed): `history.html`'s filter/prev/next buttons (real
`querySelectorAll("[data-filter]")`/`onclick` wiring),
`history.html`'s form-embedded Clear All/Delete/Copy buttons (real POST
form actions to `clear_history`/`delete_history`, or a real `onclick`),
`plans.html`'s interval-toggle and upgrade-CTA buttons (real
`[data-upgrade-btn]`/`[data-interval-toggle]` wiring; the `disabled`
CTAs — "Premium Active", "Your current plan" — are intentional status
indicators, not broken controls), `nav_app.html`'s mobile-menu toggle
(real `[data-ds-nav-toggle]` wiring, and its logout forms post to the
real `logout` view), `forget.html`/`profile.html`'s 4 id-only forms
(`otpForm`, `resetPasswordForm`, `personalInfoForm`,
`changePasswordForm`, `deleteAccountForm` — each has a real
`addEventListener`/`onsubmit` calling `fetch()` against a real, existing
endpoint: `/profile/update/`, `/profile/delete/`, `forgot_verify_otp`,
`forgot_reset_password`).

**Regression coverage:** extended the existing `test_no_dead_ui.py`
(new class `NoDeadUiAcrossWholeAppTests`, same file — reused per the
reuse-first rule rather than a new file) to fetch and check all 7
distinct page templates the app actually serves (not just `main.html`),
locking in both the fix and the widened sweep scope so a future
addition to any template is now covered by the same check that
previously only watched `main.html`.

**Files changed:** `templates/signup.html`,
`accounts/tests/test_no_dead_ui.py`.

---

## ITEM 7 — Logout/navigation event + broader GET-mutation audit — **ALREADY CORRECT (verified, not re-trusted)**

Re-read `test_phase8_nav_logout_safety.py` in full (not just re-running
it): unlike Item 6's gap, its `AUTHENTICATED_PAGES` list already covers
all 5 real authenticated pages (`/home/`, `/dashboard/`, `/history/`,
`/plans/`, `/profile/`), walks every real `<a href>` on each proving
none point at `/logout/`, and proves the logout control itself is a
`<form method="post">` (both the desktop bar and mobile-panel copies) —
genuinely exhaustive already, no scope gap like `test_no_dead_ui.py`
had.

Widened the check beyond just logout to the general "no GET mutations"
requirement: read every view in `accounts/views.py`,
`accounts/billing_views.py`, and `accounts/entitlement_views.py` for
method-gating. Every state-changing endpoint is either `@require_POST`
or does an internal `request.method != "POST"` → `405` check before any
mutation: `user_logout`, `save_history`, `delete_history`,
`clear_history`, `forgot_send_otp`/`forgot_resend_otp`/
`forgot_verify_otp`/`forgot_reset_password`, `profile_update`,
`profile_change_password`, `profile_delete`, `profile_avatar`,
`billing_views.create_order_view`/`verify_payment_view`. Read-only views
(`plans_page`, `payment_status_view`, `entitlements_view`, `plans_view`,
`costs_view`) correctly have no such gate. No gap found — nothing to
fix, no new test needed (existing coverage already proves the one
concrete historical bug in this class — logout — stays fixed).

**Files changed:** none (audit-only item).

---

## ITEM 8 — CSS/white-page root-style audit — **CLEAN (verified, nothing to fix)**

Built the actual dependency map rather than assuming: `base_public.html`/
`base_app.html` both load `tokens.css` → `components.css` → `shell.css`
in that order (correct cascade — tokens define custom properties first,
components consume them, shell composes page chrome last);
`billing.css` is loaded separately only where billing UI is rendered.
6 templates (`dashboard/forget/history/main/profile/verify_otp`) carry
their own `<style>` block on top of that for page-specific rules.

Checked each specific risk the master prompt names, against the real
files, not the (partially stale) comments inside them:
- **Duplicate `:root`/token blocks:** `tokens.css`'s own header comment
  says this consolidation was still pending as of Phase 2 ("Phase 3" to
  do it) — but grepping every page template directly (not trusting that
  comment) confirms zero of `login/signup/forget/profile/dashboard/
  history/verify_otp.html` define their own `:root` block today; the
  Phase 3 consolidation the comment describes as pending already
  happened in a later phase. One token system, no duplicates.
- **Duplicate top-level class selectors:** none across
  `components.css`/`shell.css`/`billing.css` (checked programmatically).
- **Page-local `<style>` shadowing shared components:** none — no class
  name defined inside any template's inline `<style>` block collides
  with a class defined in `components.css`/`shell.css`.
- **White-on-white / invisible text:** every hardcoded `color: #fff` /
  `white` / `#ffffff` in any inline `<style>` block is paired with a
  colored (`var(--primary-color)`/`var(--danger-color)`) button
  background, not a light one — no instance found. No stray
  `prefers-color-scheme` query exists that could mismatch a light-theme
  token against a dark background (this app has one coherent light
  theme, no dark mode).
- **Load order:** identical, correct order in both base templates —
  nothing loads shell.css before tokens.css, no page skips a shared
  file.
- **Competing breakpoints:** `768px` (matching `tokens.css`'s
  `--breakpoint-md` reference value — CSS custom properties can't
  legally appear inside `@media` conditions, so the token exists as a
  documented reference and every real usage was checked to still match
  it exactly, no drift) plus a few page-specific sub-breakpoints
  (400/420/640px) for extra-small-screen fine-tuning nested under it —
  not competing, a normal responsive-design pattern.
- **`!important` overrides:** exactly 3 uses, all inside a single
  `prefers-reduced-motion: reduce` accessibility block in `main.html`
  (forcing animations off regardless of specificity is the correct,
  standard use of `!important` for that specific case) — no override
  war anywhere else in the codebase.

**Conclusion:** the CSS architecture is already in a genuinely
consolidated, single-source-of-truth state from prior phases. No fix
was needed, so no file was touched and no new test was added for this
item — audit-only.

**Files changed:** none.

---

## ITEM 9 — Backend-backed UI connection audit — **CLEAN (verified, nothing to fix)**

Enumerated every route in `accounts/urls.py` and traced each to its
frontend caller, rather than assuming the obvious ones (credit pill,
history) were the only ones worth checking:

- `credits/balance/` → `static/js/credits.js`'s credit pill (`DS.api.get`)
- `history/delete/<id>/`, `history/clear/` → real `<form method="POST"
  action="{% url ... %}">` in `history.html` (uses the `{% url %}` tag,
  not a hardcoded path, so it can't silently drift from the route)
- `profile/data/`, `profile/update/`, `profile/change-password/`,
  `profile/delete/`, `profile/avatar/` → real `fetch()`/`onsubmit`
  handlers in `profile.html` (already confirmed under Item 6)
- `forgot/*` (all 4) → real `fetch()` handlers in `forget.html`
  (already confirmed under Item 4)
- `api/entitlements/`, `api/ai/costs/` → real `fetch()` calls in
  `main.html`'s entitlement-banner/cost-hint logic
- **`api/billing/create-order/`, `api/billing/verify-payment/`,
  `api/billing/payment-status/<id>/`, `api/subscription/`,
  `api/subscription/cancel/`, `api/subscription/restore/`** — this is
  the one genuinely non-obvious check: these billing/subscription
  backend endpoints already exist in full (from an earlier phase,
  predating this Phase 8A session — not something this session built),
  and the master prompt defers *building new* Razorpay/billing work to
  Phase 8B. Grepping `plans.html`/`profile.html` directly for these
  URLs found nothing, which would have read as "orphaned backend, no
  UI" — but that grep missed the actual wiring: both pages load a
  shared `static/js/billing.js` (`plans.html`'s upgrade buttons +
  interval toggle call `window.BillingFlow.create(...).upgrade()`,
  which itself calls `create-order`/`verify-payment`/`payment-status`)
  and `profile.html` additionally loads `static/js/profile-billing.js`
  (real `fetch()` calls to `subscription/cancel`/`subscription/restore`,
  and renders the billing card from `profile/data/`'s payload). Genuinely
  connected already — re-verifying this (not building anything new) is
  in-scope for Item 9 without touching 8B's actual remaining work
  (webhook hardening, checkout-flow UX, etc.).

No orphaned backend endpoint and no UI control calling a nonexistent
endpoint was found anywhere in the app.

**Files changed:** none.

---

## ITEM 10 — Acceptance gate cross-check + canonical docs — **COMPLETE**

All 6 remaining canonical docs the master prompt names have been
created (this session's PHASE_8A_BUSINESS_RULES.md,
PHASE_8A_SYSTEM_ARCHITECTURE.md, PHASE_8A_GENERATOR_CONTRACT.md,
PHASE_8A_CREDIT_ENTITLEMENT_AUDIT.md, PHASE_8A_AUTH_OTP_AUDIT.md,
PHASE_8A_SECURITY_AUDIT.md, PHASE_8A_TEST_AUDIT.md — none existed
before this phase; `PHASE_8A_PROGRESS.md` itself was already being
kept live throughout). Before writing the security doc, three
additional real-code checks were run fresh (not carried over from
assumption): XSS in AI-generated output (checked — every generator's
`onSuccess` handler escapes via `escapeHtml()` before `innerHTML`),
session fixation + open redirect on login (checked — both already
correct), and avatar upload validation (checked — real Pillow
content-verification already in place, not metadata-only). No new bugs
found by these three; folded into PHASE_8A_SECURITY_AUDIT.md.

### Master prompt's own completion-gate checklist, cross-checked against Items 1-9

- [x] exactly one correct navigation system per page — Item 1
- [x] duplicate nav UI fixed at the root cause — Item 1 (root cause was
      CSS, not template architecture; template tree was already single-
      source, documented precisely as such, not glossed over)
- [x] all five generators traced end-to-end — Item 3
- [x] Photo works — Item 3 (via pre-existing `test_ai_photo_integration.py`)
- [x] Story works — Item 3
- [x] Application works — Item 3
- [x] Proposal works — Item 3
- [x] Code works — Item 3
- [x] correct feature slugs — Item 3 (contract matrix)
- [x] correct API contracts — Item 3
- [x] correct CSRF — Item 7 audit + Item 2's CSRF test update
- [x] correct entitlement enforcement — Items 2, 5
- [x] anonymous generation cannot bypass business rules — Item 2
- [x] anonymous generation cannot mutate authenticated credits — Item 2
      (no `CreditAccount` at all is created for a guest anymore)
- [x] no fake credit balances — Item 2 (balances were real, not fake;
      the policy generating them was what changed)
- [x] credit service remains single source of truth — verified no
      `credit_service_v2`/parallel calculator exists anywhere
- [x] credit race conditions addressed — Item 5
- [x] provider is never called before authorization/credit eligibility — Item 2
- [x] failure/refund behavior correct — pre-existing, unchanged by this
      phase's fixes (shared infra every generator already used), re-
      confirmed still green in the full suite
- [x] retry behavior correct — same as above; also Item 5's new test
      proves a race-losing retry gets a graceful 409, not a 500
- [x] reset behavior correct — pre-existing generator state-machine
      tests, unaffected, still green
- [x] forgot-password validates real reset eligibility — Item 4
- [x] nonexistent user cannot receive a usable OTP — Item 4 (already
      true) + Item 4's new fix closes the adjacent verify-step leak
- [x] OTP is real, rate-limited and single-use — Item 4
- [x] no dummy OTP flow — Item 4
- [x] all visible buttons have real behavior — Item 6
- [x] CSS white-page issues traced and fixed — Item 8 (traced; none
      found beyond Item 1's nav-panel case, already fixed there)
- [x] major flows pass — Flows A (guest→auth gate), B (authenticated
      full cycle), C (insufficient credits), D (provider failure/no
      false success), E (forgot-password happy path), F (invalid
      account), G (navigation) are all covered by the combination of
      Items 1-7's tests; no separate end-to-end flow file was created
      since each flow maps directly onto tests that already exist
      per-item rather than needing a duplicate integration harness
- [x] security sweep complete — PHASE_8A_SECURITY_AUDIT.md
- [x] focused regression tests pass — confirmed per-item throughout
- [x] full test suite passes — 792 tests, OK
- [x] manage.py check passes — confirmed
- [x] migration check passes — confirmed
- [x] documentation updated — this item

### Final full verification (this checkpoint, immediately before packaging)

```
python manage.py check                            -> 0 issues
python manage.py makemigrations --check --dry-run  -> No changes detected
python manage.py test accounts                     -> 792 tests, OK (skipped=9)
```

**Files changed:** `PHASE_8A_BUSINESS_RULES.md`,
`PHASE_8A_SYSTEM_ARCHITECTURE.md`, `PHASE_8A_GENERATOR_CONTRACT.md`,
`PHASE_8A_CREDIT_ENTITLEMENT_AUDIT.md`, `PHASE_8A_AUTH_OTP_AUDIT.md`,
`PHASE_8A_SECURITY_AUDIT.md`, `PHASE_8A_TEST_AUDIT.md` (all new).

---

## STATE: PHASE 8A — COMPLETE

Every item in the master prompt's execution order (1-15) and every box
in its completion gate is satisfied against the evidence recorded
across this document and the 6 companion docs above. Next: package the
full original project (not a changed-files-only zip) as
`AI_GENERATORS_PHASE8A_COMPLETE_UPGRADE.zip`, extracted and re-verified
independently before delivery, per the master prompt's explicit
packaging instructions. Phase 8B (Razorpay/billing completion, full
product UI) was deliberately not started, per the master prompt's own
instruction.

## UNRESOLVED BLOCKERS
None.
