---
name: session-report
description: Template and standards for docs/reports/prompt-NN.md, the session report that replaces synchronous human supervision. Use at the end of every prompt or when running /checkpoint.
---

# Session reports

The report is the review. Ashwin has ~5 hrs/week and is not watching the build
happen — he reads this instead. A report that hides a shortcut wastes the only
oversight the project gets.

## Template — `docs/reports/prompt-NN.md`

```markdown
# Prompt NN — <name>
Branch: prompt-NN · Gate: PASS/FAIL · Date: YYYY-MM-DD

## Built
What now exists that didn't before. Concrete, file-level where useful.

## Decisions made autonomously
Each meaningful choice and the reasoning. **This is the section Ashwin
actually reviews** — it's where he learns what the system became.

## Assumed
Every default chosen for something the prompt left unspecified. Node version,
thresholds, library picks, naming. If it was a guess, it belongs here.

## Deviations from the guide
Anything that differs from the build plan, and why. Material deviations link to
docs/guide-amendments/NNN-*.md.

## Open questions for the human
Things worth a decision — each with your recommendation, so the answer can be
one word.

## Gate status
PASS/FAIL per success criterion, with measured values.
Whether `make test-gpu` was run locally (CI cannot test GPU paths).

## Risks / known gaps
What could bite later. Be specific. "Might need refactoring" is not a risk;
"the cut planner assumes CFR input — VFR sources are normalized on ingest, but
nothing enforces it if ingest is bypassed" is.
```

## Standards

- **Honest over flattering.** Report what didn't work, what you skipped, and
  what you're unsure about. A clean-looking report that omits a hack costs more
  time later than an honest one costs now.
- **Numbers, not adjectives.** "Sync improved" is noise. "Max cut drift 22ms,
  down from 90ms" is a report.
- **Bugs fixed in earlier prompts' code get named here** — fixing them is inside
  your autonomy, but Ashwin needs to know it happened.
- **Never include a secret**, a credential, a real `.env` value, or an absolute
  path containing the OS username. The repo is public.
- Keep it to what a person can read in 3–5 minutes. Depth in the "Decisions"
  section; brevity everywhere else.

## At taste gates (04, 05, 06, 08, 10)

Record Ashwin's verdict **and every specific override he made**. Those
overrides are the highest-signal taste data the style profile (P3) will ever
get. Capture the specifics — "preferred cooler grade on the dim rack clips",
not "wanted changes".
