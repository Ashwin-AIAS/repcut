# Amendment 002 — Prompt 01 scope reduced by what Prompt 00 already delivered
Date: 2026-07-31
Affects: Prompt 01 (Role & Context, Deliverables 1, 2, 6, 7, 8, Success Criteria)
Status: ACCEPTED

## What the guide says

Prompt 01, Role & Context:

> Start from an empty folder.

Prompt 01, Deliverables (abridged):

> 1. Monorepo layout: `engine/`, `ui/`, `data/`, `docs/reports/`, `Makefile`
> 2. `CLAUDE.md` at root — exactly the template from Section 6 of this guide
> 6. `Makefile`: `make dev`, `make verify-01`, `make test`, `make lint`
> 7. Git: init, `.gitignore`, initial commit on `main`, branch `prompt-01`
> 8. GitHub Actions CI: lint + typecheck + unit tests on PR

## What we found

The folder is not empty. Prompt 00 — itself an amendment to the guide
(amendment 000) — is merged and tagged `prompt-00-done`. It delivered
`CLAUDE.md`, `.claude/` (12 agents, 10 commands, 9 rules, 8 skills),
`.gitignore`, `.gitattributes`, `.editorconfig`, `.env.example`,
`.pre-commit-config.yaml`, `LICENSE` (AGPL-3.0), `README.md`, the three GitHub
Actions workflows, `scripts/precommit_guard.sh`, `scripts/verify_00.sh`, and a
`Makefile` with placeholder targets.

Six of Prompt 01's nine deliverables therefore already exist on `main`.

## Why the guide's version doesn't work

Run verbatim, Prompt 01 recreates files that Prompt 00 owns. The concrete
losses would be:

- `CLAUDE.md` overwritten with the Section 6 template — dropping the P1–P5
  invariants block, the secrets policy, the `@`-imports of `.claude/rules/*`,
  the autonomy protocol and the git/CI contract that the harness depends on.
- `.gitignore` regenerated — dropping the media, model-weight, build-plan and
  `docs/reviews/**` blocks that keep a **public** repository free of user
  footage and personal paths.
- `ci.yml` replaced — dropping amendment 001's scaffold-tolerance fix, and
  risking the `gitleaks` job that `secrets.md` declares non-negotiable.

Two sources of truth for the same files, resolved by whichever ran last. That
is the failure mode amendment 000 predicted; this amendment records it landing.

## Proposed change

**Amend Prompt 01, Role & Context:**

> ~~Start from an empty folder.~~ **This is not an empty folder. Prompt 00 is
> merged and tagged. Verify and extend the files it owns; never recreate them.**

**Amend Prompt 01, Deliverables** — each guide deliverable is classified:

| # | Guide deliverable | Status in Prompt 01 |
|---|---|---|
| 1 | Monorepo layout: `engine/`, `ui/`, `data/`, `docs/reports/`, `Makefile` | **PARTIAL** — `data/`, `docs/reports/` and a placeholder `Makefile` are `SUPERSEDED BY PROMPT 00`; `engine/` and `ui/` are **IN SCOPE** |
| 2 | `CLAUDE.md` — Section 6 template | **SUPERSEDED BY PROMPT 00** — verify content, extend only, never overwrite |
| 3 | `engine/`: FastAPI + `GET /health` + structlog + `.env.example` keys | **IN SCOPE** (`.env.example` itself is `SUPERSEDED BY PROMPT 00`; add a key only if `Settings` needs one) |
| 4 | `ui/`: Next.js skeleton with a status page rendering every `/health` field | **IN SCOPE** |
| 5 | `scripts/check_env.py` | **IN SCOPE** |
| 6 | `Makefile`: `dev`, `verify-01`, `test`, `lint` | **IN SCOPE** — replace Prompt 00's placeholder bodies; leave `help`, `secrets`, `clean`, `verify-00` untouched |
| 7 | Git init, `.gitignore`, initial commit, branch `prompt-01` | **SUPERSEDED BY PROMPT 00** except creating the `prompt-01` branch |
| 8 | GitHub Actions CI | **SUPERSEDED BY PROMPT 00** — per amendment 000, Prompt 01's job is to make the existing `engine` and `ui` jobs *execute for real* and go green, not to author workflows. `gitleaks.yml` is never touched |
| 9 | `docs/reports/prompt-01.md` | **IN SCOPE** |

**Add to Prompt 01, Deliverables:**

> 10. `scripts/verify_01.sh` — the executable gate. The guide lists Prompt 01's
>     success criteria as prose only; per `.claude/rules/testing.md` a prompt's
>     real specification is `make verify-NN`, so the script is a deliverable.

**Amend Prompt 01, Success Criteria** — add:

> - `bash scripts/verify_00.sh` still exits 0, 13/13. Prompt 01 must not
>   regress Prompt 00.

**Add to Prompt 01, Constraints:**

> - Never modify `CLAUDE.md`, `.claude/rules/*`, `.claude/agents/*`,
>   `.claude/commands/*`, `.claude/settings.json`, `.gitignore`, `LICENSE`,
>   `scripts/verify_00.sh`, or `.github/workflows/gitleaks.yml`.

## Consequences

- Prompt 01 reduces to: `engine/`, `ui/`, `scripts/check_env.py`, real
  `Makefile` targets, `scripts/verify_01.sh`, and the session report.
- No already-passed gate is invalidated. `verify_00.sh` is a criterion of
  `verify_01.sh`, so the reduction is enforced rather than merely promised.
- Later prompts are unaffected — the delivered shape of the repo after Prompt
  01 is the same shape the guide intended, reached without destroying Prompt 00.

## Principle check

Touches **P4** positively: it is the reason `.gitignore`'s media, review-page
and build-plan blocks survive Prompt 01. Overwriting them on a public repo is
precisely how user footage or a personal path leaks.

Touches **P5** neutrally — no dependency, service or account changes. Does not
bend P1, P2 or P3.
