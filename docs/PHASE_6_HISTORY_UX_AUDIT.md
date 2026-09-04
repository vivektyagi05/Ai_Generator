# PHASE 6 STEP 6.6 — History Workspace UX Audit

Status legend: CODE-VERIFIED (read/traced directly) · TEST-VERIFIED
(executed, passing test) · NOT DONE.

## What existed before this batch

`history.html` already had: list, search, client-side type filter,
pagination, a real (if unconfirmed) Delete and Clear All backed by real
POST endpoints scoped to `request.user`, and the legacy-classification
badge added in the earlier `ChatHistory.feature` work. It also had a
non-functional decorative "Favorite" button (`toggleFav()` — toggled a
CSS class and button text only, no backend field, lost on refresh) and
no way to reopen a saved result into its generator.

## What this batch added — CODE-VERIFIED + TEST-VERIFIED

**Feature badges.** `history_view()` now derives `item.feature_label`
("Story"/"Application"/"Proposal"/"Code"/"Photo"/"Chat") from the
authoritative `ChatHistory.feature` field, shown as a real badge next to
each entry — not just the existing type icon (text/image/code), which
couldn't distinguish Story from Application from Proposal (all "text").
Legacy rows (no feature on record) get no badge, consistent with the
existing "don't invent a classification the evidence doesn't support"
rule from the earlier `ChatHistory.feature` work.

**Reopen.** Each entry with a real, reopenable feature gets a
`Reopen in <Generator>` link — `<a href="/home/?generator=<feature>">`
— which is **the exact same deep-linking mechanism already built and
tested in Phase 6.7**, not a second parallel routing system. Legacy rows
(no feature on record) and `"chat"` rows (a valid `/api/ai/` feature with
no corresponding tab in `main.html`) show an explicit
"Can't reopen (legacy entry)" label with a tooltip explaining why,
instead of guessing which generator to send them to.

**Real delete/clear confirmation.** Both destructive forms now carry
`onsubmit="return confirm(...)"` with a specific message — previously a
single accidental click submitted the form immediately with no
confirmation at all.

**Removed the fake Favorite button.** `toggleFav()` and its call site are
gone. This is exactly the "decorative control that doesn't actually
work" this step calls out for removal — it never persisted anything, so
keeping it would mean shipping a control whose visible state lies to the
user after any refresh.

**XSS safety — proven, not assumed.** `{{ item.response }}` and
`{{ item.query }}` were already inside Django's default autoescaping
(no `{% autoescape off %}` or `|safe` anywhere in the template —
CODE-VERIFIED by grep before writing any test). This batch adds an
explicit test that injects `<script>alert('xss')</script>` and
`<img src=x onerror=alert(1)>` as stored `response`/`query` values and
asserts the raw tag never appears in the rendered page and the escaped
`&lt;` form does — proving the existing protection, not just asserting
its absence of failure.

**Cross-user isolation — proven, not assumed.** `delete_history()` and
`clear_history()` were already scoped to `user=request.user`
(CODE-VERIFIED, unchanged by this batch). This batch adds an explicit
test: a second user attempting to delete the first user's history item,
or calling Clear All, cannot affect the other user's rows, and cannot
see them in their own history list.

All of the above: `accounts/tests/test_history_workspace.py`, 13/13
passing. Full suite: **492 tests, OK, skipped=9** (up from 479), zero
regressions.

## What this batch did NOT do

- **Empty/loading/error states beyond what already existed.** The
  `{% empty %}No history found.{% endfor %}` block already covers the
  empty-list case; no new loading-state UI was added (the page is
  server-rendered, not fetched via JS, so there's no client-side loading
  gap to fill the way `main.html`'s generators have one).
- **Visual redesign onto `tokens.css`/`components.css`.** `history.html`
  still has its own independent gradient/card CSS, same as `main.html` —
  unifying the visual language across pages is Phase 6.8, not attempted
  here.
- **Pagination against a real backend.** The existing pagination is
  client-side-only (`perPage=5`, slicing already-rendered DOM nodes) —
  fine for a personal history list at today's likely scale, but not
  server-side pagination. Not changed this batch; flagging it as a
  known, pre-existing simplification rather than silently endorsing it
  as sufficient at any scale.
- **A "download" action for reopened generator input.** Reopen sends the
  user to the correct generator tab; it does not pre-fill the original
  prompt/inputs into that generator's form (the backend doesn't store
  the original per-field inputs, only the final built `query` string and
  the `response` — pre-filling would mean either storing structured
  input per generator, which doesn't exist, or trying to reverse-parse
  the natural-language `query` string, which would be guessing).
