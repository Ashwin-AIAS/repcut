# Documentation

| Path | What it holds | Written by |
|---|---|---|
| [`architecture.md`](architecture.md) | How the engine, UI, jobs and data fit together | maintainers |
| [`reports/`](reports/) | One session report per build stage (`prompt-NN.md`): what was built, decisions, what was assumed, gate results; plus security reviews and walkthroughs | the agent, at the end of each stage |
| [`guide-amendments/`](guide-amendments/) | Numbered records of every deliberate deviation from the private build plan, with the reason | `/guide-amend` |
| [`manual-checks/`](manual-checks/) | `[HUMAN]` checklists per stage, and `VERIFY-LATER.md` — everything waiting for the deferred human pass | checklists generated; boxes ticked only by the maintainer |
| [`future-prompts/`](future-prompts/) | Notes parked for a later stage, so they are not lost | the agent |
| [`prompts/`](prompts/) | Reusable instructions for running the agent (autonomous loop, stage kick-offs, fixes) | maintainers |
| [`reviews/`](reviews/) | Human taste-review pages. **Gitignored** — they contain stills from real footage | `scripts/*_review.py` |
| [`chat-context.md`](chat-context.md) | Status handoff block for starting a planning chat | maintainers |
| [`publish-repo.md`](publish-repo.md) | One-time bootstrap for publishing the repository | maintainers |

## Conventions

- Every report and amendment is written in our own words. The build plan is
  private and is never transcribed here — not titles, not deliverables, not
  schedules (amendment 006). `scripts/check_plan_leak.py` enforces it.
- Paths are repo-relative or `$DATA_DIR`-relative, never a machine path.
- Amendments are numbered (`NNN-slug.md`) and carry a header with date,
  what they affect, and a status: `PROPOSED` until the maintainer accepts it.

Engineering rules that bind all code live in [`../.claude/rules/`](../.claude/rules/).
