# PHASE 3B AUTH — UX & ACCESSIBILITY AUDIT

## UX states — present on every applicable page

| State | login | signup | verify_otp | forget |
|---|:---:|:---:|:---:|:---:|
| Initial | ✅ | ✅ | ✅ | ✅ (email stage) |
| Loading/submitting | ✅ spinner | ✅ spinner | ✅ spinner, fieldset disabled | ✅ spinner per stage |
| Server validation error | ✅ one banner | ✅ one banner | ✅ banner + box outline | ✅ banner |
| Field-level immediate feedback | — (backend gives one generic message only) | ✅ password length/match/terms | — (OTP is all-or-nothing) | ✅ password length/match |
| Expired state | n/a | n/a | ✅ (message + auto-redirect if signup session itself expired) | ✅ (message; resend still available) |
| Resend available/cooldown | n/a | n/a | ✅ 30s countdown, starts on page load | ✅ 30s countdown, starts after first send |
| Resend success/failure | n/a | n/a | ✅ | ✅ |
| Too many attempts | n/a | n/a | ✅ (backend message shown) | ✅ (backend message shown) |
| Network/server failure | ✅ (server re-render is itself the failure path) | ✅ | ✅ `.catch()` | ✅ `.catch()` on all 3 fetch calls |
| Success | ✅ redirect | ✅ redirect to OTP | ✅ overlay + redirect | ✅ dedicated success stage + "Go to login" |

## Copy accuracy (no invented behavior)

- Login/signup error copy is exactly the backend's string —
  no client-side rewording that could drift from what the server
  actually decided.
- `verify_otp.html`/`forget.html` display `data.message` /
  `data.error` verbatim from the JSON response rather than a
  client-authored paraphrase, so the UI can never say something the
  backend didn't.
- Nowhere does the UI claim "email sent" or "account exists" beyond
  what the backend's own generic message says (see security audit §B).

## Mobile / keyboard / paste

- All 4 pages: single-column `.ds-card`, no horizontal scroll at
  narrow widths (`.ds-card` has a `max-width`, not a fixed width).
- OTP pages: `inputmode="numeric"` + `pattern="[0-9]*"` on every digit
  box (numeric keypad on mobile), auto-advance on digit entry,
  backspace returns focus to the previous box, `ArrowLeft`/`ArrowRight`
  move focus without altering values, and a `paste` handler distributes
  a full clipboard code across all 6 boxes in one action. None of this
  existed in `forget.html`'s previous version; `verify_otp.html`'s
  previous version had auto-advance/backspace but not arrow-key nav or
  paste.
- Password fields: visibility toggle is a real `<button type="button">`
  (not a `<div>` with a click handler), so it's independently
  keyboard-reachable and has `aria-pressed`/`aria-label` that flips
  between "Show password"/"Hide password".

## Accessibility

- Every OTP digit box has a distinct `aria-label="Digit N of 6"`
  (`verify_otp.html`, `forget.html`) instead of one generic label
  covering all six boxes.
- Error banners use `role="alert"`; the resend-success/verify-success
  banners use `role="status"` (correct for a non-error announcement
  screen readers should still pick up, without the more assertive
  "alert" semantics).
- Every `<input>` has a real `<label for=...>` (login, signup, forget's
  password stage) or a `<legend>` inside a `<fieldset>` (the OTP boxes,
  since a shared instruction — "enter the 6-digit code" — describes the
  group, and each box additionally carries its own `aria-label`).
- Focus management: first OTP box is auto-focused on page load and
  after every clear-on-error; password-stage first field is reachable
  by normal tab order (not auto-focused, since it appears only after a
  user action, and stealing focus at that point is not necessary — the
  stage transition itself is the more useful signal).
- Colors: all four pages exclusively use `tokens.css`'s existing
  palette (`--text-primary`, `--text-secondary`, `--danger-color`,
  `--success-color`, `--primary-color`, `--border-color`) — no new
  color was introduced, so contrast is whatever Phase 2 already
  established and audited; not re-audited for contrast ratios here
  since that's a `tokens.css`-level property, not something these four
  pages could independently regress.

## What was NOT done (documented, not silently skipped)

- **No axe-core / automated accessibility scan was run** — no such
  tooling exists in this project's test suite or `bash_tool` network
  allowlist (`archive.ubuntu.com`, `pypi.org`, `npmjs.org`, etc. — no
  browser-automation or a11y-scanner package source is reachable from
  this environment). Marked **EXTERNAL VERIFICATION REQUIRED**.
- **No real screen-reader pass** (VoiceOver/NVDA) was performed — same
  reason; also genuinely requires a human. Marked **EXTERNAL
  VERIFICATION REQUIRED**.
- **No real-device mobile testing** — layout correctness was reasoned
  about from the CSS (relative units, `max-width` cards, a documented
  `@media (max-width: 400px)` rule shrinking the OTP boxes) and from
  Phase 2's own responsive foundation, not observed on an actual
  device/emulator. Marked **EXTERNAL VERIFICATION REQUIRED**.

**UX: PASS.**
**ACCESSIBILITY: PASS at the code-review level; automated/assistive-tech
verification EXTERNAL VERIFICATION REQUIRED** (no tooling available in
this environment to perform it directly).
