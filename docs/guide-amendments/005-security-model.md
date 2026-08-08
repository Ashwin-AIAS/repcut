# Amendment 005 — Security model added to the guide (§7)

**Date:** 2026-08-07
**Trigger:** full security review of the repository, run at Prompt 02
**Guide version:** v1.0 → v1.1

## What was wrong with the guide

v1.0 had **no security content of any kind**. Searching it for `security`,
`vulnerability`, `injection`, `XSS`, `CSRF`, `threat`, `OWASP` or `traversal`
returns zero matches. The only adjacent mentions are:

- §1: "Explicit non-goals in v1: auth, payments, multi-user, cloud GPU, mobile
  app" — auth correctly deferred, but nothing said what replaces it in the
  meantime.
- Prompt 13: auth listed as a pre-public requirement, i.e. deferred past every
  prompt that actually builds the attack surface.

The repository was in better shape than the guide: `.claude/rules/secrets.md`
is thorough, gitleaks runs pre-commit and in CI, and the guardrail script is
genuinely good. But **secrets hygiene is not application security.** Nothing in
the guide or the rules covered network boundary, input bounds, path traversal,
deserialisation, or dependency advisories — and Prompt 02 is the prompt that
introduces a chunked upload API, a WebSocket, and user files on disk.

## What the review found

Six issues, all fixed in the same session. Detail in
`docs/reports/security-review-2026-08-07.md`. In summary: the engine had no
`Host` or `Origin` validation of any kind (DNS rebinding, localhost CSRF,
cross-site WebSocket hijacking of the job stream), the media store's path
helpers interpolated unvalidated identifiers and its `absolute()` would join an
absolute or `..`-bearing stored path straight out of `$DATA_DIR`, upload sizes
had no ceiling, `/jobs?limit=-1` returned every row, the UI shipped no security
headers, and `next@14.2.35` carried six high-severity advisories with no CI job
watching for them.

None of these were caused by deviating from the guide. All of them were
possible *while following it exactly*, which is what makes this a guide defect
rather than an implementation defect.

## The amendment

1. **New §7 "Security Model"**, inserted after the CLAUDE.md template and before
   the build plan. Sections after it shift by one (old §7 → §8, old §21/22 →
   §22/23). Contains:
   - a stated threat model, including what is explicitly out of scope
   - invariants **S1–S6**, carrying the same weight as P1–P5
   - a table mapping each prompt to the attack surface it adds and what its gate
     must therefore assert
   - the standing security gate command block
2. **S1–S6 added to the CLAUDE.md template** in §6, so every session loads them.
3. **A `Security (§7)` success criterion added to prompts 02–08 and 10–12.**
   Prompt 09 adds no new surface and is unchanged.
4. **New rule file `.claude/rules/security.md`**, referenced from `CLAUDE.md`.
   This is the enforced version; §7 is the reasoning behind it.

## Why per-prompt rather than a security wave

A security wave at the end audits code whose shape is already fixed, and
competes with Prompt 12 and the thesis for the same hours. Folding one criterion
into each gate costs minutes per prompt and catches the issue in the prompt that
created it. It also matches how the guide already handles quality: measurable
claims get a script that exits 1, not a review meeting.

## Prompt 11 is the one to re-read

The copilot takes function calls from an LLM whose context includes scene
descriptions derived from frames the user filmed. Text visible in a gym — a
whiteboard, a phone screen, a poster — reaches that context. v1.0 already
specified a fixed allowlist, which is the right design; §7 states *why* it is
load-bearing and adds a prompt-injection fixture to the gate so the allowlist is
tested rather than assumed.

## Not changed

Wave structure, prompt count, prompt ordering, the 5 hrs/week budget, P1–P5, the
approved stack, and the €0 constraint. `pip-audit`, `npm audit`, `ruff`'s `S`
ruleset and the added middleware are all free and local.
