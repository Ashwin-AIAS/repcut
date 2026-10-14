# Contributing to Repcut

Repcut is built in numbered, gated stages. This file is the practical summary;
the binding rules live in [`.claude/rules/`](.claude/rules/) and win any
disagreement with this page.

## Before you start

1. Read the security section of the [README](README.md#️-security-policy--read-before-your-first-commit).
   The repository is public; a committed secret is a burned secret.
2. Read the five design principles (P1–P5) in the README. A change that breaks
   one is rejected however good it is otherwise.
3. Set up the environment:

   ```bash
   cp .env.example .env          # your own values; never commit it
   make setup                    # idempotent
   pip install pre-commit && pre-commit install
   make check-env                # every failure comes with a named fix
   ```

### Windows: run `make` from Git Bash

Every recipe in the `Makefile` is POSIX shell — `[ … ]` tests, `$?`, `&&`
chains, backslash continuations. GNU Make picks its shell by searching `PATH`
for `sh.exe`; when it cannot find one it silently falls back to `cmd.exe`,
which cannot parse any of that. The failure is not obvious — you get
`-d was unexpected at this time.` and `Error 255`, not a missing-shell message.

Pinning `SHELL` in the `Makefile` does **not** fix this. The `make` commonly
installed on Windows is GnuWin32 GNU Make 3.81 (2006), whose Windows port
re-derives the shell from `PATH` and discards the assignment: with `sh.exe`
off `PATH`, a `Makefile` pinning `SHELL := /usr/bin/bash` still reports
`SHELL_IS=sh.exe` and fails identically to an unpinned one. So the requirement
is documented rather than enforced in the file.

**Launch `make` from a Git Bash shell** (or WSL, or any shell with `sh.exe` on
`PATH`). `bash scripts/verify_NN.sh` works directly and bypasses `make`
entirely if you need it.

### Where local files go

Keep anything that must never be published — real footage, the private build
plan, scratch databases — under `local/` at the repo root. It is gitignored as
a whole. Media the app works on lives in `$DATA_DIR`, which should sit
**outside** the repo and outside any cloud-sync folder.

## Branches

- One stage = one branch, named `feat/<slug>` (or `docs/<slug>`, `fix/<slug>`,
  `chore/<slug>`): 2–4 lowercase words in our own words. Check a new name with
  `python scripts/check_plan_titles.py <file-containing-the-name>` first.
- Branches **stack**: each starts from the previous stage's branch tip, not
  from `main`, until the deferred human review pass.
- Push your branch freely. **Never push to `main`**, never force-push, never
  rewrite history, never delete a branch.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <imperative summary>
```

- `type`: `feat fix docs test refactor perf ci chore`
- `scope` (optional, short): `engine`, `ui`, `gemini`, `colour`, `gate`, …
- No stage number in the subject. The body explains *why* when it is not obvious.
- Commit granularly. Never `--no-verify`: if a hook fails, fix the cause.

## Code standards

| | Python (`engine/`, `scripts/`) | TypeScript (`ui/`) |
|---|---|---|
| Version | 3.11 | TS `strict: true` |
| Lint | `ruff` (incl. bandit `S` rules) + `mypy` clean | `eslint` + `tsc` clean, no `any` |
| Boundaries | Pydantic v2 models with bounds | Zod-parse every API response |
| Errors | named exceptions only, fixed user-facing messages | human-readable cause, never a trace |

Cross-cutting rules worth knowing before you write code:

- **FFmpeg** is only ever invoked through `engine/repcut/media/ffmpeg_builder.py`
  (`list[str]` argv, `shell=False`). See [`ffmpeg.md`](.claude/rules/ffmpeg.md).
- **Paths** come from `engine/repcut/media/store.py`, never string-built.
- **GPU** code fits a 4 GB VRAM budget, has a CPU fallback, and its tests are
  `@pytest.mark.gpu`. See [`gpu-vram.md`](.claude/rules/gpu-vram.md).
- **Gemini** receives one sampled frame per scene, cached and rate-limited, and
  is always mocked in tests. See [`gemini-usage.md`](.claude/rules/gemini-usage.md).
- **UI** uses our own design tokens and Tailwind core only — no component
  libraries, no copied assets. See [`frontend-and-licensing.md`](.claude/rules/frontend-and-licensing.md).

## Tests

```bash
make test        # CPU suite — what CI runs
make test-gpu    # GPU-marked tests, local machine only
make lint        # ruff + mypy + eslint + tsc
```

- Never commit media. Fixtures are generated at test time with
  `ffmpeg -f lavfi` from `engine/tests/conftest.py`.
- Never mark a failing test `skip`/`xfail`, and never lower a threshold, to get
  green. Fix the code, or record why the threshold was wrong as an amendment.

## Gates

Every stage's specification is an executable gate, `scripts/verify_NN.sh`:

| Command | Meaning |
|---|---|
| `make verify-NN-auto` | everything a machine can check; `[HUMAN]` items print as pending |
| `make verify-NN` | the full gate — every criterion, `[HUMAN]` included |

A stage is **built** when its `-auto` gate is green and its report
(`docs/reports/`) is written. It is **merged** only through `/gate NN` after
the human review pass recorded in `docs/manual-checks/`: full gate green, CI
green, and — for GPU changes — `make test-gpu` green. Merged stages are tagged
`v0.N.0`. Only the maintainer ticks a `[HUMAN]` box.

## Changing the plan

The build plan is private and is never transcribed into the repository in any
form. If work has to deviate from it, record a proposed amendment in
`docs/guide-amendments/` (`/guide-amend`) instead of deviating silently.

## Pull requests

Open PRs against `main` using the template in `.github/pull_request_template.md`.
CI runs lint, type checks, the CPU test suite, the UI build, dependency audits
and gitleaks; all must be green.
