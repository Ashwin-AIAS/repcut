# Amendment 007 — Next.js 14 upgraded to 16; the amendment finally written
Date: 2026-08-28
Affects: Section 4 (Architecture — Stack decisions)
Status: ACCEPTED

## What the guide says

Section 4, *Stack decisions*:

> **Frontend:** Next.js 14+ (App Router) + TypeScript + Tailwind. No component
> libraries.

The CLAUDE.md template this guide specifies (Section 6) carried the same line
into the repo root, and it went on reading "Next.js 14" there until the
Prompt 02 gate corrected it in code without a paper record.

## What we found

The 2026-08-07 security review
(`docs/reports/security-review-2026-08-07.md`) found `next@14.2.35` — the last
release on the 14 line, with no patch coming — carrying six high-severity
advisories: SSRF in Server Actions and rewrites, cache poisoning in RSC
responses, XSS via CSP nonces and `beforeInteractive`, request smuggling, and
several DoS paths. Most touch features Repcut did not use yet, but nothing in
CI was watching for the day that stopped being true.

The fix was made and approved in the same session as the finding — the review
records "Fix (approved by Ashwin): upgraded to `next@16.3.0`" — and
`CLAUDE.md`'s Stack line was corrected at the Prompt 02 gate. What never
happened is this document: the guide still said "14+", a shipped and approved
deviation from it had no numbered amendment, and `docs/chat-context.md` has
carried an "UNNUMBERED AND OWED" line naming exactly this gap since.

## Why the guide's version doesn't work

"14+" was accurate the day the guide was written and stopped being accurate
the day 14.2.35 shipped as that line's last release. A stack line naming a
version with a closed patch path and open high-severity advisories is not a
target to keep building against, and every prompt after 02 that touches `ui/`
was either silently relying on an unrecorded deviation or, read literally,
building on the version the project's own review found six findings against.

## Proposed change

**Amend Section 4, Stack decisions** — replace:

> ~~**Frontend:** Next.js 14+ (App Router) + TypeScript + Tailwind~~

with:

> **Frontend:** Next.js 16 (App Router) + TypeScript + Tailwind. React stays
> on 18 — Next 16's peer range allows it, and keeping it fixed bounds the
> change to the framework itself. No component libraries.

No further change to `CLAUDE.md` is needed: its Stack line already reads
"Next.js 16 App Router", corrected at the Prompt 02 gate. This amendment is
the record that was missing, not a new change to make.

## Consequences

- Already shipped, not newly proposed. `next@16.3.0`, `eslint@9.39.5` and
  `eslint-config-next@16.3.0` are the versions in `ui/package.json` as of the
  Prompt 02 merge; `npm audit` reports 0 vulnerabilities.
- The accompanying ESLint flat-config migration (`.eslintrc.json` →
  `eslint.config.mjs`, required because Next 16 removed `next lint`) is
  likewise already merged. No UI change is owed by this amendment — it closes
  the paper gap, not a code gap.
- Every prompt from 03 onward inherits Next.js 16 as the *recorded* stack
  rather than an approved-but-undocumented deviation from it.

## Principle check

**P5 (€0)** — unaffected. The upgrade is free; no new paid dependency.

**P1–P4** — untouched. No change to what the editor produces, to
overridability, to taste logging, or to the privacy boundary.
