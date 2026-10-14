# Repcut

AI video editor for gym content. Upload raw footage, pick a theme (or a
reference video you want to look like), get back a natural-looking,
beat-synced, captioned, postable edit. Every AI decision is a default you can
override — and overrides re-sync the rest of the edit automatically.

Local-first: everything runs on one laptop with an RTX 3050. No cloud GPU, no
paid infrastructure.

> **Status:** pre-1.0, under active development. Features land one gated stage
> at a time; see [`CHANGELOG.md`](CHANGELOG.md) for what exists today.

---

## ⚠️ Security policy — read before your first commit

**This repository is public.** Anything pushed here is world-readable forever,
including after deletion (git history keeps it).

**Never commit any of the following:**

| Never commit | Where it belongs instead |
|---|---|
| `GEMINI_API_KEY` or any API key | `.env` (gitignored) |
| GitHub tokens, PATs, SSH keys | Your OS keychain / GitHub settings |
| OAuth secrets, bearer tokens, cookies | `.env` |
| Database URLs / connection strings | `.env` |
| Private, tunnel, or ngrok URLs | `.env` |
| `.env`, `.env.local`, `.env.production` | Nowhere — gitignored by design |
| Gym footage, user video, photos | `$DATA_DIR/` (outside the repo) |
| Music files | `$DATA_DIR/music/` — also a licensing issue |
| Absolute paths containing your username | Config via env vars |

`.env.example` lists key **names with empty values only**. It must never
contain a real or sample credential.

Protection is layered so a mistake gets caught three times:

1. `.gitignore` — blocks env files, `data/`, `local/`, all media extensions
2. `pre-commit` + `gitleaks` — blocks the commit locally
3. `.github/workflows/gitleaks.yml` — blocks the push in CI

Never bypass these with `git commit --no-verify`.

**If a secret does get committed:** stop, assume the key is permanently
compromised, revoke and rotate it at the provider immediately, then tell the
maintainer. Rewriting history is not a fix — the value was already public.

To report a vulnerability, see [`SECURITY.md`](SECURITY.md).

---

## Stack

| Layer | Choice |
|---|---|
| UI | Next.js 16 (App Router), React 18, TypeScript (strict), Tailwind, Zod |
| Engine | FastAPI (async), SQLite, SQLAlchemy + Alembic, structlog |
| Rendering | FFmpeg (all encode/filter work, libx264 for exports) |
| GPU models | RIFE (slow-mo), YOLO-pose (reframe, rep count) — PyTorch/CUDA |
| Speech | faster-whisper (local) |
| Analysis | PySceneDetect (scenes), librosa (beats), silero-vad (ducking) |
| Scene understanding | Gemini 3.5 Flash, free tier, cached + rate-limited |

How the pieces fit together: [`docs/architecture.md`](docs/architecture.md).

## Design principles

- **P1 Natural only** — enhance what the camera captured, never generate or
  replace it. Banned forever: generative fill, background replacement,
  body/face editing, object insertion.
- **P2 AI recommends, user decides** — every AI choice is an overridable
  default; overrides re-sync dependents.
- **P3 Overrides are taste signals** feeding the style profile.
- **P4 Privacy** — footage stays local. Only per-scene sampled frames go to
  Gemini, and this is disclosed in the UI.
- **P5 €0** — free tiers and open source only.

## Repository layout

```
repcut/
├── engine/              FastAPI engine (Python package `repcut`)
│   ├── repcut/          application code — api/, media/, analysis/, grading/,
│   │                    music/, edit/, captions/, export/, db/
│   ├── alembic/         database migrations
│   └── tests/           pytest suite (GPU tests marked, skipped in CI)
├── ui/                  Next.js editor
│   ├── app/             routes (App Router)
│   ├── components/      feature components + design-system primitives
│   └── lib/             API client, Zod schemas, hooks, formatting
├── scripts/             dev tooling, verification gates, review builders
├── docs/                reports, amendments, manual checks — see docs/README.md
├── .github/             CI workflows, PR and issue templates
├── .claude/             agent harness: rules, skills, agents, commands
├── data/                default $DATA_DIR placeholder (contents gitignored)
├── Makefile             every developer entry point
└── local/               per-machine scratch, gitignored whole (not in the repo)
```

## Prerequisites

| Need | Notes |
|---|---|
| Python 3.11+ | |
| Node 20+ | |
| FFmpeg with libx264 | `ffmpeg -version` must list `--enable-libx264` |
| NVIDIA driver + CUDA | Optional. The engine runs CPU-only with reduced speed. |
| `make` | **Not native on Windows.** Use Git Bash, WSL, or `choco install make`. |
| `gitleaks` | Installed by `pre-commit`; also used by `make secrets`. |

**Windows:** run `make` from **Git Bash** (or WSL). PowerShell and `cmd.exe`
are not supported — the recipes are POSIX shell, and the common Windows `make`
silently falls back to `cmd.exe` when `sh.exe` is not on `PATH`. The full
explanation is in [`CONTRIBUTING.md`](CONTRIBUTING.md#windows-run-make-from-git-bash).

## Getting started

```bash
cp .env.example .env      # then fill in your own keys — never commit this file
make setup                # venv + engine[dev] + UI deps (idempotent)
pip install pre-commit && pre-commit install
make check-env            # diagnoses the environment, a named fix per failure
make migrate              # bring $DATA_DIR/repcut.db up to the current schema
make dev                  # engine on :8000, UI on :3000
```

Optional GPU acceleration: `make setup-gpu` installs a CUDA build of PyTorch.

## Everyday commands

```
make dev          # run engine + UI
make test         # full CPU test suite
make test-gpu     # GPU-marked tests, local machine only
make lint         # ruff + mypy + eslint + tsc
make format       # ruff format + prettier
make secrets      # gitleaks over the working tree and full history
make help         # every target, including the per-stage gates
```

## Development workflow

Work proceeds one numbered stage at a time, each on its own `feat/<slug>`
branch stacked on the previous one. A stage is *built* when
`make verify-NN-auto` is green, and *merged* to `main` only after the human
review pass, through `/gate NN`. Full rules: [`CONTRIBUTING.md`](CONTRIBUTING.md).

`main` is branch-protected: PR + green CI required.

## Documentation

| Where | What |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | How the engine and UI fit together |
| [`docs/README.md`](docs/README.md) | Index of every folder under `docs/` |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Branches, commits, gates, local setup |
| [`SECURITY.md`](SECURITY.md) | Threat model summary, how to report a vulnerability |
| [`CHANGELOG.md`](CHANGELOG.md) | What has shipped |
| [`.claude/rules/`](.claude/rules/) | The binding engineering rules |

## Not in v1

Auth, payments, multi-user, cloud GPU, mobile app, Docker. Docker and the
deployment path are documented (not executed) in a later stage.

## License

**GNU Affero General Public License v3.0** (AGPL-3.0).

You may use, modify and redistribute Repcut. If you run a modified version as a
network service, you must make your modified source available to its users.

See `LICENSE` for the full text. Third-party dependencies keep their own
licenses; user-supplied music is governed by its own terms and is never
distributed with this repository.
