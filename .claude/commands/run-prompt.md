---
description: Start a build prompt — load it, plan, branch, execute autonomously
argument-hint: <NN>  e.g. /run-prompt 03
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent
---

Start build prompt **$1**.

## Steps

1. **Verify position.** Confirm `prompt-$(printf %02d $(($1-1)))-done` is tagged
   and `main` is clean. If the previous prompt is not gated, stop and say so —
   prompts assume the previous one is merged and green.

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

3. **Branch.** `git checkout main && git pull && git checkout -b prompt-$1`

4. **Plan first.** Produce an implementation plan before writing code:
   - file-by-file changes
   - which specialist agents you will delegate to and for what
   - how each Success Criterion will be verified by `make verify-$1`
   - anything ambiguous, with the default you intend to assume
   Present it and wait for approval. This is the cheapest review point.

5. **Execute autonomously** after approval. Per the autonomy protocol:
   decide implementation details, fix bugs anywhere you find them, choose
   sensible defaults and record them, iterate tests until green.

   **Stop and ask only for:** P1–P5 conflicts, anything paid, contradictory
   requirements, HUMAN REVIEW checkpoints (prompts 04, 05, 06, 08, 10),
   destructive actions outside the repo, or anything touching a credential.

6. **Delegate** to the specialist agents rather than doing everything inline —
   `video-pipeline-engineer` for FFmpeg, `gpu-model-engineer` for models,
   `color-scientist` for grading, `frontend-engineer` for `ui/`,
   `audio-music-engineer` for timing, `gemini-steward` for API calls,
   `engine-architect` for routes/jobs/schema. Where tracks are independent, run
   them in parallel.

7. **Author the gate** via `gate-runner` as you build, not at the end.

8. When the work is done, run `/verify $1`, then `/checkpoint`, then `/gate $1`.

Never commit a secret. Never push to `main`. Never `--no-verify`.
