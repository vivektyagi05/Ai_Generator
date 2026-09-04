# PHASE 7 GENERATOR UX AUDIT

Per-generator consistency check, verified against actual code rather
than assumed from the shared controller's existence.

| Capability | Code | Photo | Story | Application | Proposal |
|---|---|---|---|---|---|
| Canonical lifecycle (`runGeneratorLifecycle`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Workspace shell (`.editor-section`/`.sidebar`) | ✅ (fixed this session) | ✅ | ✅ | ✅ | ✅ |
| Purpose subtitle | ✅ | ✅ | ✅ | ✅ | ✅ |
| Required/optional field markers | ✅ (1 field) | ✅ (1 field) | ✅ (2 required, 1 optional) | ✅ (4 required, 1 optional) | ✅ (4 required, 2 optional) |
| Cost hint before submission | ✅ | ✅ | ✅ | ✅ | ✅ |
| Live status badge | ✅ | ✅ | ✅ | ✅ | ✅ |
| Result heading | ✅ | ✅ | ✅ | ✅ | ✅ |
| Escaped, safe result rendering | ✅ (pre-existing) | N/A (image) | ✅ (pre-existing) | ✅ (pre-existing) | ✅ (pre-existing) |
| Result wrapper element | `<pre>` | image | `<pre>` (normalized this session) | `<pre>` | `<pre>` |
| Copy action | ✅ (pre-existing) | N/A | ✅ (added this session) | ✅ (added this session) | ✅ (added this session) |
| Download action | ✅ | ✅ | ✅ | ✅ | ✅ |
| Share action | N/A | ✅ | ✅ | ✅ | ✅ |
| Reset action | ✅ | ✅ | ✅ | ✅ | ✅ |
| Error rendering (401/exhaustion/rate-limit/generic) | ✅ | ✅ | ✅ | ✅ | ✅ |
| History save + reopen | ✅ | ✅ | ✅ | ✅ | ✅ |
| Deep-link (`?generator=`) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Duplicate-submission guard | ✅ | ✅ | ✅ | ✅ | ✅ |

## Deliberate, correct asymmetries (not bugs)

- **Photo has no Copy action**: an image result can't be meaningfully
  "copied as text" the way Story/Application/Proposal/Code text can;
  Download already covers the real use case. Not adding a fake Copy
  button here was a deliberate choice, not an oversight.
- **Code has no Share action**: Share (`navigator.share()`) was never
  implemented for Code in Phase 6; not added this session either, since
  doing so would mean inventing new functionality outside this session's
  scope of fixing real, identified gaps.
- **Field counts differ**: each generator's required/optional field
  counts come directly from its own `validate()` function -- Photo/Code
  have exactly one required field (the prompt), Application/Proposal
  have several, matching their real forms. Not flattened to be
  identical, per the brief's own instruction not to make generators
  identical when their actual inputs differ.

## Verification method

Every row above was checked by reading the actual generator's HTML
block and its `runGeneratorLifecycle()` call/`onSuccess` handler in
`templates/main.html`, not inferred from the shared controller alone.
The two "✅ (pre-existing)" / "✅ (fixed/added this session)"
distinctions reflect what was actually found versus what already
existed, consistent with the rest of this session's documentation.
