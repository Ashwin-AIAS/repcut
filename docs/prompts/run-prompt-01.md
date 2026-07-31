# Prompt 01 readiness — and the prompt to run

## Verdict: NOT ready. Two hard blockers, three soft ones.

State verified in the repo on 2026-07-28:

| Check | State |
|---|---|
| `prompt-00-done` tag | ✅ exists |
| CI fix (`b0212c4`) merged to `main` | ✅ on `main`, branch deleted |
| `main` clean | ✅ (one untracked file, see S3) |
| `verify_00.sh` | ✅ present, 10 criteria |
| `.env` | ❌ **does not exist** |
| Prompt 01 text vs. repo reality | ❌ **contradicts the guide** |
| Amendments 000, 001 | ⚠️ still `PROPOSED` |
| `docs/finalize-prompt-00.md` | ⚠️ untracked leftover |
| `make`, Python 3.11, Node 20, ffmpeg, CUDA on the G17 | ⚠️ unverified from here |

---

### B1 — `.env` is missing (hard blocker)

`/run-prompt 01` step 2 reads the build guide from `$REPCUT_GUIDE_PATH` in `.env`.
There is no `.env`, so the command cannot load the prompt.

Claude Code **cannot** create it — `.claude/settings.json` denies
`Read`/`Write`/`Edit` on `.env` by design. That guardrail stays. You do this:

```
copy .env.example .env
```

Then open `.env` and set exactly two values:

```
REPCUT_GUIDE_PATH=./Repcut_Prompt_Guide_v1.md
GEMINI_API_KEY=<from Google AI Studio — free tier>
```

Keep `REPCUT_GUIDE_PATH` **relative**. An absolute path contains your Windows
username, and `secrets.md` forbids that appearing in any config.

`GEMINI_API_KEY` is not used until Prompt 07, but `check_env.py` (a Prompt 01
deliverable) reports it as `set: true/false`, so set it now and forget it.

---

### B2 — the guide's Prompt 01 contradicts the repo (hard blocker)

Guide Prompt 01 opens with *"Start from an empty folder"* and lists as
deliverables: `CLAUDE.md`, `.gitignore`, `git init`, initial commit, GitHub
Actions CI, `Makefile`.

**All six already exist.** Prompt 00 (amendment 000, which is not in the guide)
built them. Run Prompt 01 verbatim and Claude Code will overwrite `CLAUDE.md`,
regenerate `.gitignore`, and replace `ci.yml` — destroying the harness, the
gitleaks integration, and amendment 001's scaffold-tolerance fix.

Fix: run the **amended** Prompt 01 below, which scopes Prompt 01 down to what
is genuinely missing and writes amendment 002 recording the change. Per project
convention, deviations from the guide get amended, never silently applied.

Prompt 01 reduces to: `engine/`, `ui/`, `scripts/check_env.py`, real `Makefile`
targets, `scripts/verify_01.sh`, and the session report.

---

### S1 — amendments still `PROPOSED`

`docs/guide-amendments/000` and `001` are both `PROPOSED`. That is your call,
not Claude Code's. Recommendation: accept both — 000 describes a harness that
demonstrably exists and passes its gate; 001 fixed a CI bug that is merged and
green. Leaving them `PROPOSED` means the guide's record disagrees with `main`.

The prompt below asks you once and flips them if you say yes.

---

### S2 — local toolchain unverified

I cannot see your Windows machine from here. Before starting, confirm in a
terminal at the repo root:

```
make --version
python --version      (need 3.11.x)
node --version        (need 20.x)
ffmpeg -version       (need libx264 in the configuration line)
nvidia-smi
```

`make` is the one that actually blocks: every gate in this project is a make
target. If it's absent → `winget install GnuWin32.Make` (add to PATH) or use
WSL. Do **not** let anything rewrite the Makefile into loose scripts to work
around it.

The rest are diagnosed properly by `check_env.py`, which Prompt 01 builds — so
if one is missing, the prompt still runs and the gate tells you exactly what to
install.

---

### S3 — untracked leftover

`docs/finalize-prompt-00.md` is untracked. Harmless, but `main` should be clean
before branching. The prompt below commits it into `docs/prompts/`.

---

## Do this, in order

1. Create `.env` (B1) — 2 minutes, only you can do it.
2. Run the toolchain checks (S2) — 1 minute.
3. Open a **fresh** Claude Code session in the repo folder.
4. Paste everything below the line.

Do **not** use `/run-prompt 01` for this one — its step 2 loads the guide's
unamended Prompt 01, which is the thing that's wrong. Use the prompt below;
it follows the same protocol with the scope corrected.

---
---

# PROMPT 01 (AMENDED) — Engine & UI Scaffold, Dev Environment

## Role & Context

```
You are a senior full-stack ML engineer scaffolding Repcut, a local-first AI
video editor: FastAPI engine (:8000) + Next.js 14 UI (:3000) on one Windows
laptop with an RTX 3050 (4GB VRAM).

This is NOT an empty folder. Prompt 00 is merged and tagged `prompt-00-done`.
Already built and NOT to be recreated: CLAUDE.md, .claude/ (12 agents, 10
commands, 9 rules, 8 skills), .gitignore, .gitattributes, .editorconfig,
.env.example, .pre-commit-config.yaml, LICENSE (AGPL-3.0), README.md, the
three GitHub Actions workflows, scripts/precommit_guard.sh, scripts/verify_00.sh,
and a Makefile whose targets are placeholders.

The build guide's Prompt 01 says "start from an empty folder" and lists
CLAUDE.md, .gitignore, git init and CI as deliverables. That text predates
Prompt 00 and is superseded. Deliverable 0 below records this as amendment 002.

Read CLAUDE.md and every file in .claude/rules/ before planning. They are
binding. Work autonomously per the Autonomy Protocol below. Plan first, then
execute without per-file confirmation.
```

## Deliverables

**0. Amendment 002 — scope correction.**
Write `docs/guide-amendments/002-prompt-01-scope-vs-prompt-00.md` following the
format of amendments 000 and 001. Record: the guide's Prompt 01 assumes an empty
folder; Prompt 00 already delivered repo hygiene, CLAUDE.md and CI; Prompt 01 is
therefore reduced to engine + UI + env-check + real Makefile targets + gate.
List each guide deliverable as `SUPERSEDED BY PROMPT 00` or `IN SCOPE`. Status:
`ACCEPTED` (Ashwin approved this scope by issuing this prompt). Do this first —
it is the justification for everything else in the session.

**1. Housekeeping.** `docs/finalize-prompt-00.md` and `docs/run-prompt-01.md`
are untracked. Move both to `docs/prompts/` and commit as
`docs: archive prompt-00 finalization and prompt-01 brief`. Working tree clean
before you branch.

**2. Branch.** `git checkout main && git pull && git checkout -b prompt-01`.

**3. `engine/` — FastAPI, Python 3.11.**
- `engine/pyproject.toml`: deps `fastapi`, `uvicorn[standard]`, `pydantic`,
  `pydantic-settings`, `structlog`, `httpx`; `[dev]` extra `pytest`,
  `pytest-asyncio`, `ruff`, `mypy`. Configure `ruff` and `mypy` (strict) here.
  Register pytest markers `gpu` and `slow` here.
- `engine/repcut/config.py`: pydantic-settings `Settings` reading every key in
  `.env.example`. **Never log or expose a secret value** — expose
  `gemini_api_key_set: bool` only. No hardcoded paths anywhere.
- `engine/repcut/logging.py`: structlog JSON. No `print()` in engine code.
- `engine/repcut/main.py`: FastAPI app, `GET /health` returning
  `engine_version`, `ffmpeg_version`, `ffmpeg_has_libx264`, `cuda_available`,
  `gpu_name`, `vram_free_mb`, `vram_total_mb`, `torch_device_active`
  (`"cuda"` or `"cpu"`), `data_dir_writable`, `gemini_api_key_set`.
  Pydantic v2 response model. **`/health` must return 200 with
  `cuda_available: false` and `torch_device_active: "cpu"` on a machine with no
  GPU and no torch installed** — import torch defensively, catch `ImportError`
  and `RuntimeError` by name, never bare `except`.
  Shell out to ffmpeg with `subprocess.run(list[str])`, never `shell=True`;
  `FileNotFoundError` → `ffmpeg_version: null`, not a crash.
  All I/O async; the ffmpeg/torch probes go to a thread executor.
- `engine/tests/`: `conftest.py` with an async test client fixture;
  `test_health.py` asserting 200, full schema, and that a simulated
  no-torch/no-ffmpeg environment still returns 200. All CPU-only, no `gpu` marker.

**4. `ui/` — Next.js 14 App Router + TS strict + Tailwind.**
- `create-next-app` non-interactive: App Router, TypeScript, Tailwind, ESLint,
  no `src/`, import alias `@/*`. Node 20, npm (commit `package-lock.json`).
- `tsconfig.json` `strict: true`. **No `any`** — `unknown` + narrowing.
- Dark by default. Define color/spacing/radius/type tokens in one place
  (`app/globals.css` CSS variables + `tailwind.config.ts`). No ad-hoc hex in
  components. Tailwind core utilities only — **no component library**.
- `app/status/page.tsx`: Server Component fetching `${ENGINE_URL}/health`,
  rendering **every** field with an explicit label. Parse with a **Zod schema**
  — parse, do not cast. Engine-down renders a readable error card naming the
  cause and the fix ("engine not running — `make dev`"), never a raw traceback,
  never a bare spinner.
- Accessibility baseline: focus-visible, contrast ≥ 4.5:1, semantic markup.
- `vitest` configured with one real test (the Zod schema round-trip). `npm run
  test` must exist so CI's `--if-present` actually runs it.

**5. `scripts/check_env.py`** — prints an aligned table, one row per check,
`OK` / `FAIL` / `WARN`, and for every non-OK a **named fix** on the next line.
Checks: Python ≥ 3.11 · ffmpeg on PATH · ffmpeg built with libx264 · Node ≥ 20 ·
npm · torch importable · CUDA visible to torch · GPU name contains "3050" ·
total VRAM ≥ 3.5GB · ≥ 20GB free disk in `DATA_DIR` · `DATA_DIR` exists and is
writable · `.env` present · `GEMINI_API_KEY` non-empty (report `set: true/false`
— **never print the value**) · `make` on PATH.
Exit 1 if any **hard** check fails. GPU checks are `WARN`, not `FAIL` — a
CPU-only machine must still pass, because CI has no GPU and the engine must run
without one. Never read or echo a secret. Never print an absolute path that
contains a username — print paths relative to the repo root.

**6. `Makefile` — replace the placeholders with real targets.** Keep the
existing `help`, `secrets`, `clean`, `verify-00` exactly as they are.
- `dev` — engine + UI concurrently, both logging to the same terminal, Ctrl-C
  kills both. Must work in Git Bash on Windows.
- `test` — `pytest engine -m "not gpu" -q` + `cd ui && npm run test`
- `test-gpu` — `pytest engine -m gpu -q`
- `lint` — `ruff check engine && ruff format --check engine && mypy engine &&
  cd ui && npm run lint && npx tsc --noEmit`
- `format` — `ruff format engine && cd ui && npx prettier --write .`
- `verify-01` — `bash scripts/verify_01.sh`; remove `01` from the
  not-implemented stub list.
- `setup` — create the venv, install `engine[dev]`, `npm ci` in `ui/`, idempotent.

**7. `scripts/verify_01.sh`** — author this via the `gate-runner` agent, read
`.claude/skills/verify-gate-authoring/SKILL.md` first. Same contract as
`verify_00.sh`: `set -uo pipefail`, per-criterion `[PASS]`/`[FAIL]`, exit 1 on
any failure, idempotent, cleans up. Criteria:
1. `engine/`, `ui/`, `scripts/check_env.py` exist with the expected shape
2. `ruff check`, `ruff format --check`, `mypy engine` clean
3. `pytest engine -m "not gpu"` green
4. Engine boots on an **ephemeral port**, `/health` returns 200, response
   validates against the schema, all ten fields present. Kill the process in a
   trap so a failure never leaves it running.
5. Engine `/health` still returns 200 with torch unimportable (simulate by
   running with a `sitecustomize` stub or a monkeypatched import path) —
   proves the CPU fallback
6. `npx tsc --noEmit` clean, `npm run lint` clean, `npm run test` green
7. `next build` succeeds
8. `grep` finds no `: any` / `as any` in `ui/**/*.{ts,tsx}`
9. `python scripts/check_env.py` runs and exits 0 or 1 with a parseable table
   (do not require GPU here — this must pass in CI-like conditions)
10. No `print(` in `engine/repcut/**/*.py`
11. `git ls-files` contains no `.env`, media (`.mp4/.mp3/.mov/.wav`), model
    weights (`.pt/.onnx`), `node_modules`, or build-plan document
12. `verify_00.sh` still passes — Prompt 01 must not regress Prompt 00

**8. CI.** Do **not** rewrite `ci.yml`; amendment 001 made the `engine` and `ui`
jobs conditional on scaffolding existing. Prompt 01 creates that scaffolding, so
those jobs now execute for real for the first time. Get them **green**. Only
touch `ci.yml` if a job needs a step it genuinely lacks (e.g. `working-directory`
or a `PYTHONPATH`), and say so in the report. **Never** touch
`.github/workflows/gitleaks.yml`.

**9. `.env.example`.** It already holds every key. Add a key only if `Settings`
needs one that isn't there — key name, **empty value**, never a plausible dummy.

**10. `docs/reports/prompt-01.md`** — per `.claude/skills/session-report/SKILL.md`.
Must include an **Assumed** section recording every default: Python env tool
(venv vs uv), package manager, Node version, ruff/mypy config choices, Tailwind
token names, how `make dev` runs two processes on Windows. Also record the
licence of every dependency added and confirm each is AGPL-compatible. No
absolute paths, no secret values, no username.

## Constraints

- **Scaffold only. No feature code.** No upload, no scene detection, no
  ffmpeg_builder, no models, no Gemini call. Resist building ahead — that is
  Prompts 02+.
- **Never modify** `CLAUDE.md`, `.claude/rules/*`, `.claude/agents/*`,
  `.claude/commands/*`, `.claude/settings.json`, `.gitignore`, `LICENSE`,
  `scripts/verify_00.sh`, or `.github/workflows/gitleaks.yml`.
- **Engine must boot with no GPU and no torch installed.** `/health` reports
  `cpu`. This is a hard requirement, not a nicety — CI has no GPU and the app
  must not be single-machine.
- **CI must never require CUDA.** GPU-dependent tests are `@pytest.mark.gpu`.
- All config from `Settings`/`process.env`. Zero hardcoded paths, zero
  hardcoded credentials, zero absolute paths in any committed file.
- Catch named exceptions only. No bare `except:` / `except Exception:`.
  A comment above each handler names the failure it prevents.
- Every dependency added must be AGPL-3.0-compatible. Check before adding.
  A GPL-incompatible or non-commercial licence is a **stop-and-flag**.
- **P5 (€0):** free tiers and open source only. Anything requiring payment, a
  card, or an account upgrade → stop and ask.
- Never `git commit --no-verify`. Never `git push --force`. Never push to
  `main` — merge only through the PR in `/gate 01`.
- Never read, write, print or infer any value from `.env`. Report
  `set: true/false` only. If a credential-shaped string appears anywhere: STOP,
  do not commit, do not push, tell Ashwin, and state the key must be rotated at
  the provider.

## Autonomy Protocol

**Fully autonomous. No human checkpoints in this prompt.**

Decide yourself, without asking: implementation details, library choices within
the approved stack, file layout, lint and type-checker configuration, all
defaults (record each under "Assumed"), bug fixes anywhere in the repo, and test
iteration until green. Fix a failure and re-run rather than reporting it back.

Delegate rather than doing everything inline: `engine-architect` for the FastAPI
app, routes and config; `frontend-engineer` for `ui/` and the design tokens;
`gate-runner` for `scripts/verify_01.sh`. Engine and UI are independent — run
those tracks in parallel. Consult
`.claude/skills/repcut-design-system/SKILL.md` before writing any UI, and
`.claude/skills/gpu-inference-4gb/SKILL.md` before writing the torch probe.

**Stop and ask ONLY for:** a P1–P5 conflict, anything paid, a dependency with an
incompatible licence, a genuinely contradictory requirement, anything touching a
credential, or a destructive action outside the repo.

Present your plan before executing. After approval, run to completion without
further check-ins.

## Success Criteria (= `make verify-01`)

- `bash scripts/verify_01.sh` → every criterion `[PASS]`, exit 0
- `bash scripts/verify_00.sh` → still 10/10, exit 0
- `make dev` boots both; `http://localhost:3000/status` renders all ten
  `/health` fields; on the ROG G17 it shows `cuda_available: true`,
  GPU name containing "3050"
- `python scripts/check_env.py` exits 0 on the ROG G17
- `make lint` clean; `make test` green
- CI green on the `prompt-01` → `main` PR, with the `Engine (Python)` and
  `UI (Next.js)` jobs actually **running** (not skipped) and passing
- `docs/reports/prompt-01.md` written, with a populated "Assumed" section and
  the dependency licence audit
- `git ls-files` contains no env file, media, model weight, `node_modules`,
  `.pdf`, `.docx`, or build-plan document

## Then

Run `/verify 01`, `/checkpoint`, `/gate 01`. Do not start Prompt 02 in this
session — Prompt 02 gets a fresh session with clean context.

---

## One thing to check after `/gate 01`

Once CI's `Engine (Python)` and `UI (Next.js)` jobs run for real, add them to
`main`'s required status checks (Settings → Branches → ruleset). Right now only
`Secret scan` and `Repo guardrails` are required — after Prompt 01 that leaves
the two jobs that test actual code unenforced.
