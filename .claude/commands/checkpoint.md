---
description: Commit work, write/refresh the session report, push the prompt branch
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

Save the current state of work.

1. **Secret scan first.** Run `gitleaks protect --staged --redact` (and
   `make secrets`). If anything trips, STOP — do not commit, do not push.
   Report it and treat any exposed key as compromised.

2. Review `git status` and `git diff`. Confirm nothing staged is: a `.env`
   file, media (`.mp4`/`.mov`/`.mp3`/…), a model weight, anything under
   `data/`, or a path containing the OS username.

3. Commit granularly with `prompt-NN: <imperative summary>` messages. Never
   `--no-verify`.

4. Write or refresh `docs/reports/prompt-NN.md`:

```markdown
# Prompt NN — <name>

## Built
<what now exists that didn't before>

## Decisions made autonomously
<each choice, and why — this is the part Ashwin actually reviews>

## Assumed
<every default chosen for something the prompt left unspecified>

## Deviations from the guide
<anything that differs, and why. If material, link the amendment in
docs/guide-amendments/>

## Open questions for the human
<things worth a decision, with your recommendation>

## Gate status
<PASS/FAIL per success criterion, with measured values>
<whether make test-gpu was run locally>

## Risks / known gaps
<what could bite later>
```

No secrets, no credentials, no absolute user paths in the report.

5. Push the prompt branch: `git push -u origin prompt-NN`. Never push `main`.

Keep the report honest. It replaces synchronous supervision — a report that
hides a shortcut wastes the only review Ashwin gets.
