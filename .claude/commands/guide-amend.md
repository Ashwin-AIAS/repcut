---
description: Record a proposed amendment to the build guide instead of silently deviating
argument-hint: [what changed and why]
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

Amendment: $ARGUMENTS

The build guide is the source of truth. When a finding contradicts it, the
answer is never a silent deviation — it is a written amendment Ashwin can
accept or reject.

## Write `docs/guide-amendments/NNN-<slug>.md`

```markdown
# Amendment NNN — <title>
Date: <YYYY-MM-DD>
Affects: Prompt(s) <NN>, section <X>
Status: PROPOSED

## What the guide says
<quote the exact text>

## What we found
<the concrete finding — measurement, error, constraint, contradiction.
Evidence, not opinion.>

## Why the guide's version doesn't work
<specific. "it was inconvenient" is not a reason.>

## Proposed change
<the exact replacement text, in the guide's own format>

## Consequences
<which later prompts this affects; what has to change downstream;
whether any already-passed gate is invalidated>

## Principle check
<does this touch P1–P5? If it bends one, say so explicitly and loudly —
that requires a conscious decision from Ashwin, not an approval by default.>
```

## Rules
- Number sequentially. Never edit an existing amendment — supersede it.
- Status stays `PROPOSED` until Ashwin marks it `ACCEPTED` or `REJECTED`.
- Reference the amendment in the session report of the prompt that raised it.
- **Never** amend a threshold merely to make a failing gate pass. If a gate is
  failing, the default assumption is that the code is wrong, not the spec.
- If the amendment would bend P1–P5, do not proceed on the assumption it will
  be accepted. Stop and ask.
