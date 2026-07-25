# Prompt 00 — Agent Harness
Branch: prompt-00 · Gate: PASS (13/13) · Date: 2026-07-25

## Built

The Claude Code harness the remaining 13 prompts execute inside, plus the
repository's safety layer. Nothing in `engine/` or `ui/` — no feature code.

| Area | Count | Contents |
|---|---|---|
| `.claude/agents/` | 12 | engine-architect, video-pipeline-engineer, color-scientist, gpu-model-engineer, frontend-engineer, audio-music-engineer, gemini-steward, principle-reviewer, gate-runner, taste-gate-prep, copilot-engineer, deploy-architect |
| `.claude/commands/` | 10 | run-prompt, verify, checkpoint, gate, debug, vram, setup-env, taste-review, guide-amend, new-prompt |
| `.claude/rules/` | 9 | secrets, principles, code-style, ffmpeg, gpu-vram, gemini-usage, git-and-ci, testing, frontend-and-licensing |
| `.claude/skills/` | 8 | ffmpeg-recipes, color-grading, beat-and-audio, gpu-inference-4gb, gemini-free-tier, verify-gate-authoring, repcut-design-system, session-report |
| `.github/workflows/` | 3 | ci.yml, gitleaks.yml, tag-gate.yml |

Plus: `CLAUDE.md` (rules `@`-imported), `README.md`, `.gitignore`,
`.gitattributes`, `.editorconfig`, `.pre-commit-config.yaml`,
`scripts/precommit_guard.sh`, `scripts/verify_00.sh`, `Makefile`,
`.env.example`, `.claude/settings.json`, `.claude/hooks/format_edited.sh`,
`.github/pull_request_template.md`, `.vscode/extensions.json`,
`data/music/LICENSES.md` (local, untracked).

## Decisions made autonomously

**12 agents, not the ~15 of the reference project.** Dropped everything
specific to a training/thesis workflow (dataset-engineer, ml-trainer,
colab-runner, rag-engineer, literature-researcher, thesis-writer,
domain-science advisors). Repcut trains no models and writes no thesis. Each
agent file is context cost on every session that loads it; the set was kept to
roles this build actually has.

**`security-auditor` folded into `principle-reviewer`.** v1 has no auth, no
multi-user, no network surface beyond one outbound API. The real security
surface is P4 (what leaves the machine) and credential hygiene on a public
repo — both are now the reviewer's first checks, ahead of the principle checks.

**Capability skills, not per-prompt skills.** Per-prompt skills would duplicate
the build guide, which already is the per-prompt instruction. These eight are
reused across all thirteen prompts.

**Rules split from CLAUDE.md and `@`-imported.** Keeps CLAUDE.md readable while
the detail stays always-in-force.

**Secrets policy placed above the design principles in CLAUDE.md.** The repo is
public; that ordering is deliberate.

**Enforcement layered three deep** rather than stated once: `.gitignore` →
`pre-commit` + `gitleaks` → CI. Any single layer can be bypassed by accident;
all three cannot.

**A gate for the harness itself (`make verify-00`).** Every prompt in this
project is defined by an executable gate. The harness had none, which made it
the one unverified thing in the repo. It now tests its own claims — including
running `git check-ignore` against a throwaway repo rather than asserting that
`.gitignore` looks correct.

## Assumed

- Repo public (confirmed by Ashwin) → unlimited free Actions minutes
- Guide, project instructions, and personal docs stay unpublished; Claude Code
  reads the guide from `REPCUT_GUIDE_PATH` in `.env`
- `CLAUDE.md`, `README.md` and `docs/reports/` are public
- Node 20, Python 3.11 in CI
- `gitleaks` v8.21.2, `ruff` v0.8.4, `pre-commit-hooks` v5.0.0, all pinned
- Docker deferred to Prompt 13; v1 develops natively on the ROG G17
- Licence: **AGPL-3.0** (Ashwin's decision). `LICENSE` file added at repo creation via GitHub's picker — verbatim text required.

## Deviations from the guide

One, recorded as `docs/guide-amendments/000-prompt-00-agent-harness.md`
(status PROPOSED):

Prompt 01 Deliverables 2 and 8 (create `CLAUDE.md`, create CI) now read
*verify and extend* rather than *create*, because both exist here. Without the
amendment, Prompt 01's first session would overwrite the secrets policy, the
rule imports, and the git/CI contract.

## Open questions for the human

1. **Licence — RESOLVED: AGPL-3.0.** Chosen so a hosted competitor cannot take
   a modified Repcut closed. The `LICENSE` file itself is not committed here:
   licence text must be verbatim to be valid, and it could not be fetched at
   authoring time. Add it via GitHub's licence picker ("GNU Affero General
   Public License v3.0") when creating the repo, or `curl` the official text
   from gnu.org. README already states AGPL-3.0.
2. **`docs/reports/` is public.** It describes decisions, not footage or keys.
   One `.gitignore` line makes it private if you'd rather.
3. **Branch protection cannot be set from a file.** Enable it in GitHub
   settings after the first push: require PR + green CI on `main`.

## Gate status

`make verify-00` → **PASSED: 13 of 13**

```
  [PASS] 12 agents present                              (12)
  [PASS] 10 commands present                            (10)
  [PASS] 9 rules present                                (9)
  [PASS] 8 skills present                               (8)
  [PASS] frontmatter valid, names match paths
  [PASS] CLAUDE.md rule imports resolve
  [PASS] CLAUDE.md carries P1-P5 + secrets policy
  [PASS] .env.example: key names, empty secret values
  [PASS] no credential-shaped strings in repo
  [PASS] gitignore blocks media/env/plan, keeps source
  [PASS] pre-commit guard blocks bad, allows good
  [PASS] workflows + pre-commit + settings parse
  [PASS] no CRLF in shell scripts / Makefile
```

`make test-gpu`: not applicable — no GPU code in this prompt.

## Risks / known gaps

- **CI jobs are skip-tolerant until Prompt 01.** `ci.yml` exits early when
  `engine/` and `ui/` are absent, so it goes green on an empty repo. Prompt 01
  must make those jobs actually execute; a green CI badge before then means
  little. `gitleaks.yml` is *not* skip-tolerant and runs from commit one.
- **CRLF is the highest-probability silent failure.** The repo lives in
  OneDrive on Windows; `.gitattributes` forces LF on `*.sh` and the `Makefile`,
  and `verify-00` asserts it. If `scripts/precommit_guard.sh` is ever checked
  out with CRLF it fails as `bad interpreter` — and the local secret guard
  disappears without an obvious error. `verify-00` is the detector.
- **`make` is not native on Windows.** Every workflow assumes it. Use Git Bash,
  WSL, or `choco install make`. Unverified on Ashwin's machine at time of writing.
- **The PostToolUse formatter hook depends on an undocumented payload shape.**
  Written to degrade to a no-op and always exit 0, so the worst case is
  unformatted code that `pre-commit` catches anyway.
- **`scripts/check_env.py` does not exist yet** — a Prompt 01 deliverable.
  `/setup-env` performs the checks inline until then.
- **GPU paths will never be exercised by CI.** Structural, not fixable:
  GitHub runners have no GPU. `make test-gpu` before every gate touching them
  is the only mitigation, and it depends on remembering. The PR template
  checkbox exists for that reason.
