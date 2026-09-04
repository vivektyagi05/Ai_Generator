# PHASE 6 STEP 6.8A — Workspace Shell Audit

Status legend: CODE-VERIFIED · TEST-VERIFIED · NOT DONE.

## What this batch did

**`main.html` now extends `base_app.html` (→ `nav_app.html`)** instead of
being a standalone HTML document with its own header/nav. This is the
first page to ever actually adopt the Phase 2 foundation — CODE-VERIFIED
before touching anything: `grep -rl "nav_app.html\|base_app.html"
templates/` returned only the two files themselves, confirming zero
existing pages used them. `AI_GENERATORS/views.py:create()` now passes
`active_nav: "home"`. The duplicate `api.js`/`credits.js` `<script>`
includes in `main.html` were removed (now loaded exactly once, by
`nav_app.html`) — TEST-VERIFIED
(`test_main_html_no_longer_duplicates_shared_scripts`).

**A real, pre-existing bug found and fixed, not introduced by this
batch, but only surfaced by it:** Django's `{# ... #}` comment tag is
single-line only — CODE-VERIFIED empirically (rendered a minimal
multi-line version directly under the real Django settings and confirmed
the raw text came through completely unstripped, rather than trusting a
read of Django's docs alone). `base_app.html` and `nav_app.html` both had
multi-line `{# #}` header comments from when they were built — invisible
until now because no page had ever rendered either template.
**Checking for the same pattern elsewhere found it in six already-LIVE
templates**: `login.html`, `signup.html`, `forget.html`,
`verify_otp.html`, `base_public.html`, `nav_public.html` — meaning
internal engineering notes (backend-contract references, exact rate-limit
numbers, security reasoning like "enumeration resistance is load-bearing")
have been leaking into the rendered HTML source of real, unauthenticated,
public-facing pages, unrelated to anything in this session's earlier
work. All nine affected templates (the two Phase 2 files, `main.html`
itself — my own first attempt at its new header comment made the
identical mistake — and the six live ones) were converted to Django's
real `{% comment %}`/`{% endcomment %}` tag.

TEST-VERIFIED: `accounts/tests/test_template_comment_bug.py`, 8/8 — every
affected page actually re-rendered and checked for leaked content (not
just re-reading the fixed source), plus structural proof `main.html`
genuinely renders `nav_app.html`'s real nav (`class="ds-nav"`,
`data-ds-credit-pill` present) rather than merely "happening" to be
clean.

**Guest-mode gap fixed in `nav_app.html` before adoption**, not after:
it previously hardcoded Logout/Profile links unconditionally, with no
guest branch — harmless while unused, but `/home/` is guest-accessible
(guests can generate; `save_history` is the one `@login_required`
restriction, not the whole page), so adopting it as-is would have shown
a guest a "Logout" link that does nothing useful and hidden their actual
Login/Signup entry points. Fixed with the same
`{% if user.is_authenticated %}` branch `main.html`'s old header already
had, both in the desktop nav and the mobile panel.

Full suite after this batch: **509 tests, OK, skipped=9** (up from 501),
zero regressions.

## What this batch did NOT do (honest gap list against the full 6.8 brief)

- **No re-theme.** `main.html`'s extensive dark-glassmorphism CSS is
  fully preserved in `{% block extra_head %}` and still governs
  everything inside the generator workspace. `tokens.css`/
  `components.css`'s actual palette (CODE-VERIFIED by reading
  `tokens.css`) is a light theme — converting the generator workspace to
  it is a real, separate, large visual redesign decision, not a
  mechanical class swap, and was NOT attempted this batch. The only
  design-system classes now live in the page are the ones `nav_app.html`
  itself brings (`.ds-nav`, `.ds-btn`, `.ds-credit-pill`) for the header
  bar only.
- **6.8B (generator navigation)**: not touched this batch — the existing
  `switchTab()`/deep-link mechanism (built in 6.7, unchanged) already
  satisfies most of 6.8B's literal requirements (works with `?generator=`,
  preserves back/forward, works from history reopen since 6.6, and now
  additionally works from the real app nav dropdown via `nav_app.html`
  since that dropdown's links were already wired to `?generator=` back
  in 6.7) — but no dedicated re-verification pass was done this batch
  specifically confirming all of those together after the shell change,
  beyond the general regression suite passing.
- **6.8C (per-generator experience consistency), 6.8D (deeper
  credit/entitlement placement beyond the header pill), 6.8E (history
  embedded IN the workspace rather than as a separate page), 6.8F (full
  dead-CSS/dead-control sweep beyond the credit-pill cleanup already
  done), 6.8G (explicit re-verification that the visual change didn't
  regress the backend contract)**: NOT attempted this batch. 6.8G in
  particular deserves an explicit note: the full regression suite
  passing (509/509) is strong evidence the backend contract is intact,
  since `test_ai_*` and `test_generator_lifecycle.py`/
  `test_generator_state_machine.py` all still pass unchanged — but no
  NEW test was written this batch specifically targeting "did the shell
  change somehow open a bypass path," because the shell change never
  touched `/api/ai/`, the controller, or the state machine at all — only
  the surrounding document structure and header.

## Honest note on scope

This batch is 6.8A plus an unplanned but real, valuable finding (the
comment-leak bug) that came directly out of doing 6.8A properly — I
would rather have found and fixed a real information-disclosure issue on
your live login page while doing 6.8A than skip verifying the shell
migration thoroughly enough to have found it. 6.8B through 6.8G, as
scoped in the current instructions, remain not done.
