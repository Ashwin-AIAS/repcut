# Repcut — CLAUDE.md

## What this is
AI video editor for gym content. User uploads raw footage, picks theme or
reference video, gets a natural beat-synced edit. Local-first: FastAPI engine
(localhost:8000) + Next.js UI (localhost:3000) on an RTX 3050 laptop.

---

## 🔴 SECURITY — ABSOLUTE, NON-NEGOTIABLE, NO EXCEPTIONS

**This repository is PUBLIC. Anything committed is world-readable forever,
including in git history after deletion.**

NEVER commit, push, print into a committed file, or paste into any tracked
file — under any circumstance, for any reason, at any point:

- API keys or secrets of any kind (`GEMINI_API_KEY`, HuggingFace tokens, any
  `sk-`/`AIza`/`ghp_`/`hf_` prefixed string)
- GitHub tokens, PATs, SSH keys, `.pem`/`.key`/`.p12` files
- OAuth client secrets, refresh tokens, session cookies, bearer tokens
- Database URLs, connection strings, or any URL containing credentials
- Private/ngrok/tunnel URLs, personal endpoints, internal links
- `.env`, `.env.local`, `.env.production` or any real env file
- Personal data: email addresses, real names in configs, file paths containing
  the user's Windows/OneDrive username, device identifiers
- User footage, gym videos, music files, or any media asset

Rules that follow from this:
1. Secrets live ONLY in `.env`, which is gitignored. `.env.example` contains
   KEY NAMES WITH EMPTY VALUES ONLY — never a real value, never a "sample" key.
2. Read config through `pydantic-settings` / `process.env`. Never hardcode.
3. Never echo a secret's value into logs, reports, test fixtures, error
   messages, comments, or `docs/reports/prompt-XX.md`.
4. If a secret is ever committed: STOP all work, tell the human immediately,
   treat the key as permanently compromised, and instruct them to revoke and
   rotate it. Do not attempt a history rewrite as a "fix" — the key is burned.
5. `gitleaks` runs pre-commit and in CI. If it fails, the fix is removing the
   secret — never adding an ignore rule or `--no-verify`.
6. Never run `git commit --no-verify` or `git push --force` to main.

Full detail: `.claude/rules/secrets.md`

---

## Design principles (INVARIANTS — never violate, stop and ask if a task conflicts)
P1 Natural only: enhance captured footage, never generate/replace content.
   Slow-mo interpolation max 2x, fallback to normal speed on low confidence.
   Banned: generative fill, bg replacement, body/face editing, object insertion.
P2 AI recommends, user decides: every AI choice is an overridable default;
   overrides re-sync dependent decisions automatically.
P3 Log user overrides as taste signals (style profile).
P4 Footage stays local; only per-scene sampled frames go to Gemini; disclose in UI.
P5 €0: free tiers, open source, local GPU only. Paid anything = stop and ask.

## Stack (approved — deviations require asking)
Next.js 14 App Router + TS + Tailwind | FastAPI async + SQLite | FFmpeg |
PyTorch/CUDA (RIFE, YOLO-pose) | faster-whisper | PySceneDetect | librosa |
silero-vad | Gemini 2.0 Flash free tier (cached, rate-limited)

## Conventions
- Python 3.11, ruff + mypy clean; TS strict; no `any`
- All processing jobs async with progress events over WebSocket
- All scripts idempotent; all paths from env/config, never hardcoded
- FFmpeg invocations built by `ffmpeg_builder.py` — never raw string concat
- Sampled-frame Gemini calls: cache by (video_hash, scene_id) in SQLite; never
  send full videos; never exceed free-tier rate (client-side limiter)
- Every prompt ends: make verify-XX green → commit → merge → tag →
  docs/reports/prompt-XX.md

## Autonomy protocol
Decide yourself: implementation details, bug fixes anywhere, defaults (record
under "Assumed" in the report), test iteration.
Stop and ask ONLY: P1–P5 conflicts, paid services, contradictory requirements,
HUMAN REVIEW checkpoints (prompts 04, 05, 06, 08, 10), destructive actions
outside repo, and ANY situation involving a secret or credential.

## Git & CI contract
- One prompt = one branch `prompt-NN`. Push freely to that branch.
- Merge to `main` ONLY through `/gate NN`: `make verify-NN` green + CI green +
  session report written. Then tag `prompt-NN-done`.
- `main` is branch-protected. Never push directly to `main`.
- GPU code never runs in CI. Mark it `@pytest.mark.gpu`; CI skips it.

## Commands
```
make dev          # run engine + UI
make verify-XX    # gate for prompt XX
make test         # full suite (CPU only)
make test-gpu     # GPU-marked tests, local machine only
make lint         # ruff + mypy + eslint + tsc
make secrets      # gitleaks scan of working tree and history
```

## Rules (always in force)
@.claude/rules/secrets.md
@.claude/rules/security.md
@.claude/rules/principles.md
@.claude/rules/code-style.md
@.claude/rules/ffmpeg.md
@.claude/rules/gpu-vram.md
@.claude/rules/gemini-usage.md
@.claude/rules/git-and-ci.md
@.claude/rules/testing.md
@.claude/rules/frontend-and-licensing.md

## Build plan
The authoritative 13-prompt build plan lives OUTSIDE this repo (it is not
published). Local path is set in `.env` as `REPCUT_GUIDE_PATH`. Amendments to
the guide are recorded in `docs/guide-amendments/`.

**Never transcribe the plan into the repository, in any form** — not as data,
not in a fixture, not in a test, not in a docstring example, not as prose in a
comment. Prompt titles, summaries, deliverables, wave structure and calendar
estimates ARE the plan; retyping them is publishing it just as surely as
committing the file. Code that needs them reads them at runtime from
`REPCUT_GUIDE_PATH` and degrades with a named message when it is absent.
Enforced by `verify-01` criterion 13 (`scripts/check_plan_leak.py`), which
matches content, not filenames. See amendment 006.

Never silently deviate from the guide. If a finding contradicts it, run
`/guide-amend` and write the amendment.
