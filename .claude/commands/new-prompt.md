---
description: Draft a future build prompt (14+) in the guide's exact format
argument-hint: <NN> <topic>  e.g. /new-prompt 14 multi-user auth
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

Draft build prompt **$1**: $2

New ideas go into the guide as future prompts. They do **not** get injected
into a wave already in progress — wave discipline is what makes a 5 hrs/week
schedule finish.

## Format — match the guide exactly

```markdown
## PROMPT $1 — <Name>

<One paragraph: what this builds and why it comes after what precedes it.>

### Role & Context
​```
<The role Claude Code takes. State what is already merged and green, and that
it works autonomously per the protocol.>
​```

### Deliverables
1. …
<Numbered, concrete, each independently checkable. No "and improve X".>

### Constraints
- <Each constraint names the failure it prevents.>

### Autonomy Protocol
<Fully autonomous, or the exact human checkpoint and what it decides.>

### Success Criteria (= `make verify-$1`)
- <Each one binary and measurable. If it can't be scripted, it is a human
  gate — say so explicitly rather than writing a criterion that can't fail.>
```

## Checks before you hand it over
- Does it fit €0 (P5)? Any paid dependency makes it out of scope — say so.
- Does it respect P1–P4? Name any tension explicitly.
- Does it fit ~5 hrs/week? If not, split it into two prompts.
- Does it depend on something not yet built? State the prerequisite prompt.
- Is every success criterion executable? Non-executable criteria are the single
  most common way a prompt silently fails.

Write to `docs/future-prompts/prompt-$1-<slug>.md`. It enters the guide only
when Ashwin moves it there.
