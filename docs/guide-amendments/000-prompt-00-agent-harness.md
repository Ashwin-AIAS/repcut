# Amendment 000 — Prompt 00: Agent Harness precedes Prompt 01
Date: 2026-07-25
Affects: Prompt 01 (Deliverables 2 and 8), Section 3 Wave Plan, Section 6
Status: PROPOSED

## What the guide says

Section 3, Wave 0: "Repo, CLAUDE.md, dev env, CI skeleton — Prompt 01"

Prompt 01, Deliverables:
> 2. `CLAUDE.md` at root — exactly the template from Section 6 of this guide
> 8. GitHub Actions CI: lint + typecheck + unit tests on PR (no GPU steps in CI
>    — GPU checks are local-only in `check_env.py`)

## What we found

The Claude Code harness — agents, slash commands, rules, skills, settings, and
the secret-scanning CI — is most useful *before* Prompt 01 runs, because
Prompt 01 is itself executed by that harness. Building it inside Prompt 01
means the first session runs unassisted and then writes the assistance it
should have had.

Additionally, the repository will be **public**, which raises secret hygiene
from a convention to a blocking CI gate. That gate needs to exist before the
first commit, not after it.

## Why the guide's version doesn't work

If Prompt 01 creates `CLAUDE.md` and CI from scratch, it will overwrite the
harness versions — which contain the secrets policy, the rules `@`-imports, and
the git/CI contract that the harness depends on. Two sources of truth for the
same files, resolved by whichever ran last.

## Proposed change

**Add to Section 3, Wave 0:**

| Wave | Features | Prompts |
|---|---|---|
| 0 — Foundation | **Agent harness (agents, commands, rules, skills, secret-scanning CI)**, repo, CLAUDE.md, dev env | **00**, 01 |

**Add Prompt 00 to Section 7's table:**

| # | Name | What gets built | Human review? | Key tech |
|---|---|---|---|---|
| 00 | Agent Harness | 12 agents, 10 commands, 9 rules, 8 skills, CLAUDE.md, README, gitleaks CI, pre-commit | report only | Claude Code config |

**Amend Prompt 01, Deliverable 2:**
> 2. `CLAUDE.md` at root — **already created in Prompt 00. Verify it contains
>    the Section 6 template content plus the P1–P5 invariants verbatim and the
>    secrets policy. Extend, never overwrite.**

**Amend Prompt 01, Deliverable 8:**
> 8. GitHub Actions CI — **`gitleaks.yml`, `ci.yml` and `tag-gate.yml` already
>    exist from Prompt 00 with scaffold-tolerant skip conditions. Prompt 01's
>    job is to make the engine and ui jobs actually execute (dependencies,
>    lockfiles, lint/test scripts present) and go green on the PR. Do not
>    replace the workflows; do not weaken or remove the gitleaks job.**

**Amend Prompt 01, Success Criteria** — add:
> - `gitleaks` CI job green; no media, env file, or model weight tracked
> - `pre-commit install` documented in README and verified by `check_env.py`

## Consequences

- Prompt 01 becomes slightly smaller: it verifies and extends rather than
  creates two of its deliverables.
- Every later prompt gains the harness from session one — specialist agents,
  `/run-prompt`, `/verify`, `/gate`, and the rules loaded via CLAUDE.md.
- Docker moves explicitly to Prompt 13 (`deploy-architect`). v1 development
  runs natively on the ROG G17; containerizing earlier adds friction and
  VRAM-passthrough complexity for zero v1 benefit.
- No already-passed gate is invalidated — nothing has been gated yet.

## Principle check

Touches **P5**: the harness is entirely local configuration and GitHub Actions
on a public repo, which has unlimited free minutes. €0 holds.

Touches **P4** positively: the secrets and media blocking added here is what
prevents user footage or paths from ever reaching a public repository.

Does not bend P1, P2, or P3.
