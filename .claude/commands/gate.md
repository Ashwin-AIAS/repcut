---
description: Close a prompt — verify, review, PR, CI, merge, tag
argument-hint: <NN>  e.g. /gate 04
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent
---

Close out prompt **$1**. Every step must pass before the next.

1. **Gate:** run `/verify $1`. Every success criterion must PASS. If the prompt
   touched GPU code, `make test-gpu` must also be green locally — CI cannot
   test it.

2. **Principle review:** delegate to `principle-reviewer` on the full diff
   (`git diff main...prompt-$1`). Verdict must be APPROVE. A BLOCK on secrets
   stops everything immediately.

3. **Human taste gate:** if $1 is 04, 05, 06, 08 or 10, run `/taste-review $1`
   and **STOP**. Do not merge until Ashwin gives a verdict. Record his verdict
   and every override he makes into the session report — those are the highest
   signal taste data in the project (P3).

4. **Report:** confirm `docs/reports/prompt-$1.md` exists and covers built /
   decisions / assumed / deviations / open questions / gate status.

5. **PR:** open `prompt-$1` → `main`. Wait for CI green — `ci.yml`,
   `gitleaks.yml`. If CI fails, fix the code; never weaken the workflow.

6. **Merge** (no force, no direct push to `main`), then tag:
   `git tag prompt-$1-done && git push --tags`

7. Report what's next: the following prompt, and whether it opens a new wave.

If any step fails, stop at that step and say exactly which one and why. A
half-gated prompt merged to `main` breaks the contract every later prompt
depends on.
