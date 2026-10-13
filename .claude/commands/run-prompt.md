---
description: Start a build prompt — load it, plan, branch, execute autonomously
argument-hint: <NN>  e.g. /run-prompt 03
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent
---

Start build prompt **$1**.

## Steps

1. **Verify position.** Under amendment 016 the previous prompt need not be
   gated: confirm its report (`docs/reports/prompt-<NN-1>.md`) ends with
   "BUILD COMPLETE (verification pending)" and its `make verify-<NN-1>-auto`
   is green, or that it is tagged (`prompt-<NN-1>-done` or `v0.<NN-1>.0`). If
   neither holds, continue the previous prompt instead of starting this one.

2. **Load the prompt.** Resolve the guide's path without touching `.env` —
   `.env` is deny-listed for `Read`/`Bash` by design (`secrets.md`), so a step
   that reads it is unreachable, not merely inadvisable:
   - If `REPCUT_GUIDE_PATH` is already exported in the shell environment, use
     it as an override.
   - Otherwise glob the repo root for `*Prompt_Guide*` (the same pattern
     `.gitignore` uses) and use the single match.
   - If neither resolves — no env var exported, and zero or more than one
     glob match — stop and say so. Never fall back to reading `.env`.

   Read PROMPT $1 in full from the resolved path: Role & Context,
   Deliverables, Constraints, Autonomy Protocol, Success Criteria. Also read
   `docs/guide-amendments/` for anything amending this prompt.

3. **Branch.** Stacked (amendment 016): start from the previous prompt's
   branch tip, never from `main`. Name it in our own words, `feat/<slug>` or
   `docs/<slug>` (amendment 015), never a guide title; run
   `scripts/check_plan_titles.py` on the name first. Put `Branch: <name>` in
   the report header.

4. **Plan first.** Produce an implementation plan before writing code:
   - file-by-file changes
   - which specialist agents you will delegate to and for what
   - how each Success Criterion will be verified by `make verify-$1`
   - anything ambiguous, with the default you intend to assume
   Plan approval is pre-granted (amendment 016): write the plan into the
   report and continue.

5. **Execute autonomously** after approval. Per the autonomy protocol:
   decide implementation details, fix bugs anywhere you find them, choose
   sensible defaults and record them, iterate tests until green.

   **Stop and ask only for:** P1–P5 conflicts, anything paid, contradictory
   requirements a proposed amendment cannot resolve, destructive actions
   (outside the repo, or on git history), or anything touching a credential.
   HUMAN REVIEW checkpoints are not stops (amendment 016): produce their
   artifacts, list every `[HUMAN]` criterion as PENDING, add the stage to
   `docs/manual-checks/VERIFY-LATER.md`, and continue.

6. **Delegate** to the specialist agents rather than doing everything inline —
   `video-pipeline-engineer` for FFmpeg, `gpu-model-engineer` for models,
   `color-scientist` for grading, `frontend-engineer` for `ui/`,
   `audio-music-engineer` for timing, `gemini-steward` for API calls,
   `engine-architect` for routes/jobs/schema. Where tracks are independent, run
   them in parallel.

7. **Author the gate** via `gate-runner` as you build, not at the end.

8. When the work is done, run `make verify-$1-auto` (it must pass), then
   `/checkpoint`. The report's last line is "BUILD COMPLETE (verification
   pending)" only when `verify-$1-auto` passed. `/gate $1` waits for the
   deferred human pass.

Never commit a secret. Never push to `main`. Never `--no-verify`. Never tick a
box in `docs/manual-checks/`.
