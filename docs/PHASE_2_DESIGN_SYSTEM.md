# PHASE 2 — Design System

## Why this palette, not a new one
GAP-03 found 4 visual languages across 8 pages. Rather than inventing a
5th, the system adopts the one that's already the majority pattern: the
neutral `#f7f7f8`/`#10a37f` token set independently duplicated in
`login.html`, `signup.html`, `forget.html`, and `profile.html`, and already
the fallback palette `static/css/billing.css` composes with
(`var(--primary-color, #10a37f)`). Centralizing it is net-zero visual risk
for those 5 pages (their computed styles don't change — see
`static/css/tokens.css`'s header comment) and gives Phase 3 one real target
to migrate `main.html`, `history.html`, and `verify_otp.html` toward.

## Files
- `static/css/tokens.css` — CSS custom properties only. Core tokens are the
  existing `:root` block verbatim; new tokens (spacing scale, semantic
  colors, radii, z-index, breakpoints) are additive, namespaced to avoid
  collision with any per-page value.
- `static/css/components.css` — component classes, all prefixed `ds-` so
  they can be dropped into an existing page without colliding with that
  page's current class names (e.g. `main.html` already has an unrelated
  `.card`).
- `static/css/shell.css` — nav, mobile nav, and the two page shells.

## Typography
`var(--font-family-base)` = the existing system-font stack
(`-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
sans-serif`), already used by 5 of 8 pages. No web font is added — this
also resolves part of GAP-03: `verify_otp.html` is the only page fetching
from `fonts.googleapis.com`, an avoidable external dependency once it
migrates to this system in Phase 3.

## Colors
| Token | Value | Use |
|---|---|---|
| `--primary-color` | `#10a37f` | primary actions, focus rings |
| `--danger-color` | `#ef4444` | destructive actions, error states |
| `--warning-color` | `#f59e0b` | low-credit state, warnings |
| `--success-color` | `#10a37f` | success alerts/toasts |
| `--info-color` | `#3b82f6` | informational alerts |
| `--text-primary` / `--text-secondary` / `--text-tertiary` | `#202123` / `#6e6e80` / `#9ca3af` | text hierarchy |
| `--bg-main` / `--bg-card` / `--bg-hover` | `#f7f7f8` / `#ffffff` / `#f3f4f6` | surfaces |

## Spacing / radius / shadow
4px-based scale (`--space-1` … `--space-10`), 3-step radius scale
(`--radius-sm/md/lg` = 6/10/14px, matching the radii already used ad hoc
across the existing pages), `--shadow-sm/md` = the existing values,
`--shadow-lg` added for modals/toasts (nothing currently needs elevation
that high).

## Components defined (`components.css`)
Buttons (`ds-btn-primary/secondary/danger/ghost`, disabled + focus-visible
states), inputs/textarea/select (`ds-input`, `ds-field-error`), cards
(`ds-card`), badges (`ds-badge-*`, including `ds-badge-premium`), alerts
(`ds-alert-*`), toasts (`ds-toast-*`, paired with `static/js/toast.js`),
modals (`ds-modal-*`), loading (`ds-spinner`, `ds-skeleton`), empty/error/
success page states (`ds-state`), focus states (`ds-focusable`), and the
credit indicator (`ds-credit-pill`, states: ok/low/empty/unlimited).

## Responsive breakpoints
`--breakpoint-sm/md/lg` = 480/768/1024px. `shell.css`'s nav collapses at
768px, matching the only breakpoint currently in use anywhere in the
codebase (`profile.html` and others informally target ~768px in their own
media queries).

## What was deliberately not done
No CSS framework or build step was introduced (Tailwind, Bootstrap, a
bundler). Step 6's instruction — "do not introduce a frontend framework
unless the existing project genuinely requires it" — doesn't apply here:
plain CSS custom properties cover every requirement above, and the project
already works entirely with Django templates + hand-written CSS/JS.
