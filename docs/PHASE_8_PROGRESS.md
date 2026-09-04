# PHASE 8 — ARCHITECTURE RECOVERY — CHECKPOINT

Status as of this pass. Nothing below is marked DONE from source
inspection alone — every DONE item has a passing, executed regression
test cited next to it. Full command run this pass:

    SECRET_KEY=... GROQ_API_KEY=... python manage.py check
    SECRET_KEY=... GROQ_API_KEY=... python manage.py makemigrations --check --dry-run
    SECRET_KEY=... GROQ_API_KEY=... python manage.py test accounts

Result: `check` clean, no missing migrations, **778 tests, 0 failures, 9
skipped** (the 9 are the existing Postgres-only concurrency tests that
self-skip on SQLite — unrelated to this pass, unchanged from before it).

---

## PRIORITY 1 — Logout root cause — **DONE / VERIFIED**

**Finding:** `accounts.views.user_logout` accepted GET. Traced every
`href="/logout/"` / `{% url 'logout' %}` in `templates/` and `static/`
(2 occurrences, both in `nav_app.html`, both intentional) — there was
no misplaced second logout link causing the reported symptom. The bug
class is structural: a state-changing action reachable by ordinary
navigation (a click, a prefetch, any future stray href) rather than a
deliberate POST.

**Fix:**
- `accounts/views.py`: `user_logout` now `@require_POST`.
- `templates/partials/nav_app.html`: both Logout controls (desktop bar,
  mobile panel) changed from `<a href="{% url 'logout' %}">` to
  `<form method="post" action="{% url 'logout' %}">{% csrf_token %}<button>`.
- `static/css/shell.css`: `.ds-nav-logout-form` + a mobile-panel button
  rule so the form/button look identical to the `<a>` it replaced — no
  visual change.
- `accounts/tests/test_phase3b_e2e_flows.py`: updated the one existing
  test that asserted GET-logout worked (that assertion described the
  bug, not the intended behavior) to POST.

**Regression coverage:** `accounts/tests/test_phase8_nav_logout_safety.py`
(5 tests) — GET to `/logout/` no longer ends the session; POST still
does; every other nav `<a>` on every authenticated page (`/home/`,
`/dashboard/`, `/history/`, `/plans/`, `/profile/`) is checked to
contain no `logout` in its href and following each one leaves the
session authenticated; the real Logout control is confirmed to be the
POST form, not an anchor, on both copies.

**Not claimed:** I could not reproduce the *original* reported click
sequence in this ZIP's current code (no CSS overlap, no stray dropdown
z-index issue, no rogue global click handler were found on trace —
`nav.js`, `api.js`, `shell.css`, `components.css`, `base_app.html` were
all checked). The GET-based logout is the only concrete defect that
matches the symptom class and was fixable at the root; it's the fix
that's now in and tested, not a confirmed reproduction of the exact
original incident.

---

## PRIORITY 2 — Guest → user credit lifecycle — **DONE / VERIFIED**

**Finding:** `accounts/services/guest_identity.py` gives every
unauthenticated caller a session-scoped `CreditAccount`. No code path
anywhere in `accounts/views.py` ever migrated that balance into the
`CreditAccount` created for a new/existing `User` on signup or login —
confirmed by grep, not assumed. A guest who spent down toward 0 and
then signed up would see a **different**, freshly-granted 50-credit
account, which is consistent with (at least half of) the reported
"0 → 50 → 0" symptom.

**Fix:**
- `accounts/models.py` (+ migration `0014_...`): new
  `CreditTransaction.TransactionType.MIGRATION`.
- `accounts/services/credit_service.py`: new
  `migrate_guest_credits_to_user(guest_key, user)` — atomic, idempotent
  (via the existing `uq_credittxn_account_reference_type` DB constraint,
  `reference_id=f"guest-migration:{guest_key}"`), moves only *available*
  balance (an in-flight reservation on the guest account is left intact
  so it still resolves correctly later), never grants a second initial
  free credit to an identity that already had one as a guest, never
  creates phantom credits from a guest account that was fully spent.
- `accounts/views.py`: `user_login()` and `verify_otp()` (the signup
  completion view) now capture the guest session key **before** calling
  Django's `login()` (which rotates the session key via `cycle_key()`)
  and call the migration function instead of the bare
  `get_or_create_account(user=...)` path.

**Regression coverage:**
`accounts/tests/test_phase8_guest_credit_migration.py` (10 tests) —
scenarios A (guest with credits → signup), B (guest with zero credits →
signup), C (guest → already-existing user account), D (repeated
migration attempt is not double-credited), "no guest activity" (both
with and without a session at all — normal initial grant unaffected),
an in-flight-reservation-survives-migration case, and three
end-to-end tests through the real `/login/` view (not just the service
function) proving the final `/credits/balance/` response — the same
endpoint the nav pill's JS reads — agrees with the migrated server
balance.

**Documented, not silently assumed, out of scope:** `AIUsage` /
`ChatHistory` rows created during the guest session keep their original
(no-user) ownership; this pass only migrates the credit balance. Noted
in the function's own docstring as a follow-up, not claimed as done.

---

## REMAINING (not started this pass)

- Priority 3 — credit display consistency audit across every frontend
  surface (nav pill, dashboard, profile, history) beyond the one
  end-to-end check already covered above.
- Priority 4 — `PHASE_8_BUSINESS_RULES.md`.
- Priority 5 — `PHASE_8_UI_BACKEND_CONTRACT.md`.
- Priority 6 — CSS architecture inventory / white-page tracing.
- Priority 7 — full page-by-page flow verification (guest, authenticated,
  failure, provider-failure, session).
- Priority 8 — fresh security sweep.
- Priority 9 — was run as part of Priorities 1–2's own verification
  (`check`, `makemigrations --check`, `test accounts`), not yet run as
  its own dedicated pass against the *remaining* priorities' changes
  (there are none yet).
- `PHASE_8_SYSTEM_ARCHITECTURE.md` — not created yet; the real system
  map (Priority 2 of the original directive) is still outstanding.

Nothing above is marked DONE. Continuing from here in dependency order
(Priority 3 next, since it depends on the migration work above) is the
next actual step — not asking which to pick.
