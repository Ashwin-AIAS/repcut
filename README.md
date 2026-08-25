# Repcut

AI video editor for gym content. Upload raw footage, pick a theme (or a
reference video you want to look like), get back a natural-looking,
beat-synced, captioned, postable edit. Every AI decision is a default you can
override — and overrides re-sync the rest of the edit automatically.

Local-first: everything runs on one laptop with an RTX 3050. No cloud GPU, no
paid infrastructure.

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

1. `.gitignore` — blocks env files, `data/`, all media extensions
2. `pre-commit` + `gitleaks` — blocks the commit locally
3. `.github/workflows/gitleaks.yml` — blocks the push in CI

Never bypass these with `git commit --no-verify`.

**If a secret does get committed:** stop, assume the key is permanently
compromised, revoke and rotate it at the provider immediately, then tell the
maintainer. Rewriting history is not a fix — the value was already public.

---

## Stack

| Layer | Choice |
|---|---|
| UI | Next.js 14 (App Router), TypeScript, Tailwind |
| Engine | FastAPI (async), SQLite, Alembic |
| Rendering | FFmpeg (all encode/filter work) |
| GPU models | RIFE (slow-mo), YOLO-pose (reframe, rep count) — PyTorch/CUDA |
| Speech | faster-whisper (local) |
| Analysis | PySceneDetect (scenes), librosa (beats), silero-vad (ducking) |
| Scene understanding | Gemini 2.0 Flash, free tier, cached + rate-limited |

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

## Prerequisites

| Need | Notes |
|---|---|
| Python 3.11+ | |
| Node 20+ | |
| FFmpeg with libx264 | `ffmpeg -version` must list `--enable-libx264` |
| NVIDIA driver + CUDA | Optional. The engine runs CPU-only with reduced speed. |
| `make` | **Not native on Windows.** Use Git Bash, WSL, or `choco install make`. |
| `gitleaks` | Installed by `pre-commit`; also used by `make secrets`. |

### On Windows, run `make` from Git Bash

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
is documented here rather than enforced in the file.

**Launch `make` from a Git Bash shell** (or WSL, or any shell with `sh.exe` on
`PATH`). PowerShell and `cmd.exe` are not supported. `bash scripts/verify_NN.sh`
works directly and bypasses `make` entirely if you need it.

## Getting started

```bash
cp .env.example .env      # then fill in your own keys — never commit this file
pip install pre-commit && pre-commit install
make verify-00            # harness self-check: 13 criteria
make dev                  # engine on :8000, UI on :3000
python scripts/check_env.py
```

`make verify-00` validates the repo's own configuration — agent/command/skill
frontmatter, rule imports, and that `.gitignore` really does block secrets,
media and the build plan (tested with `git check-ignore`, not assumed).
Run it after any change to `.claude/` or `.gitignore`.

## Development workflow

Work proceeds one numbered prompt at a time. Each prompt gets its own branch
and merges to `main` only when its gate passes.

```
/run-prompt 03     # plan + branch + build
/verify 03         # make verify-03, PASS/FAIL per criterion
/checkpoint        # commit, write session report, push branch
/gate 03           # verify + CI + PR + merge + tag prompt-03-done
```

Session reports land in `docs/reports/prompt-NN.md`. Deviations from the build
plan are recorded in `docs/guide-amendments/`.

`main` is branch-protected: PR + green CI required.

## Not in v1

Auth, payments, multi-user, cloud GPU, mobile app, Docker. Docker and the
deployment path are documented (not executed) in the final prompt.

## License

**GNU Affero General Public License v3.0** (AGPL-3.0).

You may use, modify and redistribute Repcut. If you run a modified version as a
network service, you must make your modified source available to its users.

See `LICENSE` for the full text. Third-party dependencies keep their own
licenses; user-supplied music is governed by its own terms and is never
distributed with this repository.
