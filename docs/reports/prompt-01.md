# Prompt 01 — Engine & UI scaffold, dev environment
Branch: prompt-01 · Gate: PASS (13/13) · Date: 2026-08-03

## Built

Prompt 01 was scoped down by [amendment 002](../guide-amendments/002-prompt-01-scope-vs-prompt-00.md):
six of the guide's nine deliverables were already owned by Prompt 00. What this
prompt actually added:

**Engine** (`engine/`) — FastAPI app importable as `repcut.main:app`.
- `config.py` — pydantic-settings `Settings`; `GEMINI_API_KEY` held as
  `SecretStr`, and only the derived boolean `gemini_api_key_set` may leave the
  module. Relative paths anchor to the repo root, not the process cwd.
- `models.py` — `HealthResponse`, the 10-field capability contract with the UI.
- `probes.py` — FFmpeg and torch/CUDA capability probes. Both blocking, both
  called via `asyncio.to_thread`, neither ever raises.
- `main.py` — `GET /health`, running all three probes concurrently.
- `logging.py` — structlog JSON config. No `print()` anywhere in `engine/repcut`.
- 12 tests, all CPU.

**UI** (`ui/`) — Next.js 14 App Router, TypeScript strict, Tailwind.
- `app/globals.css` — the design tokens (single source of style).
- `tailwind.config.ts` — maps utilities onto those tokens; no new values.
- `app/status/page.tsx` — renders every `/health` field, including the gaps.
- `lib/health.ts` + Zod schema parsed (not cast) at the API boundary.

**Scripts / gates**
- `scripts/check_env.py` — 15-row environment diagnostic, a named fix per
  non-OK row.
- `scripts/verify_01.sh` — the gate, 13 binary criteria.
- Real `Makefile` bodies for `dev`, `test`, `test-gpu`, `lint`, `verify-01`.

This session resumed the above mid-flight and closed it. See **Resumed state**.

## Resumed state — what was interrupted

The branch had three commits and two uncommitted fixes left by an interrupted
`make verify-01` run. Both were reviewed and found correct, then committed as
`d48498c`:

| Fix | Why it was needed |
|---|---|
| `mypy --config-file engine/pyproject.toml` in `ci.yml` | mypy discovers config from the *current directory* only. Run from the repo root, a bare `mypy engine` never loads `engine/pyproject.toml` and type-checks in **non-strict** mode — passing CI while enforcing nothing. |
| exit-5 tolerance in `make test-gpu` | pytest exits 5 on an empty collection. Prompt 01 has no GPU-marked tests, so the target failed instead of reporting nothing to run. Every other non-zero exit still fails. |

Deliverables 0–9 were already complete. The only missing deliverable was this
report.

## Decisions made autonomously

### SHELL pinning — investigated, then deliberately not done

`make` here is GnuWin32 GNU Make 3.81 (2006). `SHELL` was unpinned, so make
falls back to `cmd.exe` whenever `sh.exe` is off PATH, and every recipe silently
depends on being launched from Git Bash.

Pinning was tried and **measured**, not assumed:

| Scenario | Result |
|---|---|
| `sh.exe` on PATH, unpinned | works |
| `sh.exe` on PATH, `SHELL := /bin/bash` / `/usr/bin/bash` / `sh` / `bash` | works — but `$(SHELL)` reports `sh.exe` in **every** case |
| `sh.exe` **off** PATH, unpinned | `-d was unexpected at this time.` → Error 255 |
| `sh.exe` **off** PATH, `SHELL := /usr/bin/bash` | **identical failure**, `SHELL_IS=sh.exe` |

3.81's Windows port re-derives the shell from PATH and discards the assignment.
Pinning is a **no-op** on this toolchain: it does not fix the cmd.exe fallback,
and the only bash path available (`C:\Program Files\Git\usr\bin\bash.exe`)
contains a space and is machine-specific.

**Took the documentation path.** A line that looks like a guarantee while
providing none is worse than a documented requirement. The Git Bash requirement
is now recorded at the top of the `Makefile` and in `README.md`, with the
measurement that justifies it. `make help`, `make lint`, `make test-gpu` and
`make verify-01` were re-run green afterwards.

**`.ONESHELL` audit** (3.81 does not support it — each recipe line is its own
shell): no recipe relies on state carried between lines. Every `cd` is
`&&`-joined on a single line; `test-gpu` uses `; \` continuations to stay in one
shell. Recorded as a comment in the Makefile so it survives the next edit.

### A build-plan transcription was blocked — and the guard gap closed

**This is the finding worth Ashwin's attention.**

Mid-session, a concurrent session wrote `engine/repcut/prompts_data.py` into
this working tree — 304 lines transcribing all 15 prompt entries (67 deliverable
strings, wave titles, timelines), headed *"Derived from guide prompts.pdf"*.

CLAUDE.md states the build plan is not published. Every guard enforced that by
**filename**:

- `precommit_guard.sh` → matches `*Prompt_Guide*`, `Repcut_*`, `*.pdf`
- `.gitignore` → matches `guide*.pdf`, `*Prompt_Guide*`
- `verify-01` criterion 11 → greps `git ls-files` for those patterns

An ordinary-looking `.py` under `engine/` matched none of them, and carried no
credential, so gitleaks passed it too. Measured: `precommit_guard.sh
engine/repcut/prompts_data.py` → **exit 0, allowed**. On a public repo it would
have published the plan.

Closed the gap in both `precommit_guard.sh` and `verify_01.sh` with a **content**
check: three or more *distinct* wave titles in one file is bulk transcription.
One or two is a quotation — an amendment citing a wave — and stays allowed,
which is why it counts distinct titles rather than matching the word "Wave". A
bare `Wave N` signal was tested and rejected: it false-positives on
`.claude/agents/*` ("Deferred until Wave 3").

Measured before shipping: **0** of the tracked files match; the transcription
that slipped through matches **6**. `verify_00`'s guard contract still holds
(`a.mp4` blocked, `engine/x.py` allowed), and the check skips non-regular paths
so deletions and probe filenames do not trip it.

Per Ashwin's decision the tracker feature was **parked, not deleted** — all 7
files plus two patches preserved outside the repo, and `main.py`, `page.tsx` and
the `Makefile` restored. It needs its own prompt, and `prompts_data.py` needs
rewriting to reference prompts by number rather than copy their content.

### Bugs found in review and fixed

CodeRabbit reviewed the PR and `required_conversation_resolution` blocked the
merge until each thread was addressed. Seven were real defects, all in code
written earlier in this prompt, and all fixed before merge:

| Where | Defect |
|---|---|
| `engine/repcut/main.py` | `NamedTemporaryFile(delete=False)` + `unlink()` on the success path only. A mid-write failure (ENOSPC) left a stray `.repcut-write-probe-*` in `DATA_DIR` on **every** failed `/health` hit. Moved to `try/finally` — this was an idempotency violation. |
| `scripts/dev.sh` | **`$!` captured the wrong process.** In `cmd \| prefix &`, `$!` is the *last* command in the pipeline — the `awk` in `prefix` — so cleanup killed the log prefixer and left uvicorn and next holding :8000/:3000. Switched to `> >(prefix …)`, which makes `$!` the server. This invalidated my earlier claim that Ctrl-C cleanup worked; it killed the prefixer, not the servers. |
| `scripts/dev.sh` | `ENGINE_URL` was defaulted *before* `.env` was read, so `.env` setting `ENGINE_PORT=8010` and nothing else left the UI pointed at :8000 while the engine ran on :8010. Now derived last, after ports resolve. Verified: `.env ENGINE_PORT=8010` → `ENGINE_URL=http://localhost:8010`. |
| `scripts/check_env.py` | `out.splitlines()[0]` in three checks. A tool that exists but prints nothing → `IndexError` → traceback → exit 2, which criterion 9 counts as FAIL. Guarded with `next(iter(...), "")`. |
| `scripts/verify_01.sh` | My new criterion 13 used `for f in $(git ls-files)` — a tracked path containing a space would word-split and scan non-existent names. Switched to `git ls-files -z` + `read -r -d ''`. |
| `ui/lib/env.ts`, `ui/app/status/page.tsx` | `/status` rendered `ENGINE_URL` raw. A URL may carry `user:pass@host`, which `secrets.md` forbids displaying, and a screenshot of that page is the realistic leak path. Added `ENGINE_URL_DISPLAY`, which strips credentials; requests still use `ENGINE_URL`. Covered by 4 new tests. |
| docs | `verify_00.sh` was described as "10/10" in amendment 002 and `run-prompt-01.md`. It has had **13** criteria since it was authored (single commit in its history) — the number was wrong when written, not stale. Corrected. Untagged fences in `engine/README.md` tagged. |

UI tests went 11 → 15.

### Smaller calls

- **`check_env.py` disk check is a hard FAIL, kept that way.** It currently
  fails on this machine (13GB free, threshold 20GB). Criterion 9 accepts exit 0
  *or* 1 by design, so the gate is unaffected. Not lowered — see Risks.
- Four printed `fix:` strings used an em-dash the Windows console codepage
  renders as `?`. Switched to ASCII. Docstrings keep theirs (never printed).
- `subprocess.run(..., check=False)` made explicit in `check_env.py`, matching
  `probes.py`.

## Deferred

**torch / CUDA → Prompt 07.** Recorded in
[amendment 003](../guide-amendments/003-defer-torch-to-prompt-07.md).

The guide gates Prompt 01 on *"UI status page shows CUDA true, GPU name RTX
3050"*. Nothing before Prompt 07 (RIFE) imports torch — Prompt 06 captions use
faster-whisper, which is CTranslate2, not torch. That criterion would gate
Prompt 01 on a ~2.3GB CUDA wheel no code needs for six prompts, that no CI
runner can ever satisfy, and that risks version drift before first use. A prior
install attempt disconnected mid-download; pip does not resume, so a retry
restarts from zero.

**Deferred, not dropped — Prompt 07's gate inherits it.** Prompt 01's binding
criterion is the CPU path, which is what CI and every non-GPU machine actually
exercise. No change to `verify_01.sh` was needed: criterion 4 asserts the ten
`/health` fields and their types without constraining `cuda_available`, and
criterion 5 boots the engine with torch blocked from import and asserts
`/health` still returns 200 with `device=cpu`. Amendment 003 records that this
shape was deliberate rather than accidental.

`check_env.py` still WARNs that torch is absent, and now names Prompt 07 as when
it is needed and `make setup-gpu` as how — a WARN that explains itself.

**No torch, torchvision or torchaudio was installed this session.**

## Assumed

Defaults chosen where the prompt was silent:

| Area | Chose | Why |
|---|---|---|
| Python env | stdlib `venv` at `.venv/`, not `uv` | Zero extra tooling to install; `make setup` is idempotent and works on a bare Python 3.11. `uv` is faster but is another dependency on the critical path. |
| Engine install | `pip install -e "engine[dev]"` | Editable, so `repcut.main:app` resolves without PYTHONPATH games. |
| Node package manager | `npm` + committed `package-lock.json` | Ships with Node; CI's `setup-node` caches it natively via `cache-dependency-path`. |
| Node version | 20 LTS in CI; `engines` allows `^20.19 \|\| ^22.12 \|\| >=24` | Next 14 needs ≥18.17. Local machine runs v22.20. |
| Python version | 3.11 | Pinned by `requires-python` and CI. |
| ruff | line-length 100, `select = E,W,F,I,UP,B,ASYNC,ANN,SIM,RUF` | `ASYNC` catches blocking calls on the event loop; `ANN` enforces the annotation rule in code-style.md. 100 over 88 — signatures with Pydantic `Field(description=…)` wrap badly at 88. |
| mypy | `strict = true`, `torch.*` → `ignore_missing_imports` | Without the override every CI run fails on an import it is not supposed to require. |
| pytest | `asyncio_mode = "auto"`, markers `gpu` + `slow` | Avoids decorating every async test. |
| Tailwind tokens | `surface / panel / raised`, `line{,-strong}`, `fg-{primary,secondary,muted}`, `accent{,-surface}`, `positive / warning / danger`; `--space-N`, `--text-*`, `--radius-{lg,xl}`, `--motion-{micro,layout}` | Role-named, not value-named, so a palette change is one edit. Utilities resolve to `var(--token)`, which disables opacity modifiers (`bg-panel/50`) on purpose — a tinted surface gets a solid token whose contrast can be measured. |
| `ENGINE_URL` | server-only, **no** `NEXT_PUBLIC_` prefix | Read in Server Components only; never inlined into the client bundle. Malformed value falls back to `http://localhost:8000` rather than crashing the build. |
| vitest | Node environment, `lib/**/*.test.ts` only | Nothing renders React yet, so no JSX transform or DOM shim. `vitest run` is single-shot — watch mode would hang CI. |
| `/health` optionality | `ffmpeg_version`, `gpu_name`, `vram_*` nullable; the rest required | A field is `None` exactly when its probe can legitimately fail. |

### `scripts/dev.sh` — two processes and Ctrl-C on Windows

Assumed, since the prompt did not specify: **one terminal, both processes,
prefixed interleaved output**, rather than two terminals or a task runner like
`concurrently` (which would be another dependency for something a shell script
does).

It lives in a script rather than the Makefile because make on Windows may hand
recipe lines to `cmd.exe`, which cannot express job control or traps.

Ctrl-C on Windows is the part that needed a real decision. Git Bash reports an
**MSYS** pid, but `taskkill` needs the **native Windows** pid, which Git Bash
exposes at `/proc/<pid>/winpid`. So `kill_tree` reads winpid and calls
`taskkill //PID <winpid> //T //F` (the `//` escaping stops MSYS path-mangling),
then falls back to `kill -TERM`. Without `//T` the uvicorn `--reload` child and
the `next dev` worker survive the parent and keep ports 8000/3000 bound.

Only `ENGINE_PORT`, `UI_PORT` and `ENGINE_URL` are lifted from `.env`.
`GEMINI_API_KEY` is deliberately **not** exported to the UI process — the engine
reads it itself via pydantic-settings.

## Dependency licence audit

Repcut is **AGPL-3.0**. Every dependency below is permissive (MIT / BSD-3-Clause
/ Apache-2.0) and therefore one-way compatible *into* an AGPL-3.0 work. No
copyleft-incompatible licence, no licence forbidding commercial or hosted use,
and nothing paid — **P5 holds**. Versions are as installed and verified from
package metadata, not from memory.

**Engine — runtime** (`engine/pyproject.toml`)

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| fastapi | 0.141.1 | MIT | yes |
| uvicorn[standard] | 0.52.0 | BSD-3-Clause | yes |
| pydantic | 2.13.4 | MIT | yes |
| pydantic-settings | 2.14.2 | MIT | yes |
| structlog | 26.1.0 | MIT OR Apache-2.0 | yes |
| httpx | 0.28.1 | BSD-3-Clause | yes |

**Engine — dev**

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| pytest | 9.1.1 | MIT | yes |
| pytest-asyncio | 1.4.0 | Apache-2.0 | yes |
| ruff | 0.16.1 | MIT | yes |
| mypy | 2.3.0 | MIT | yes |

**UI — runtime** (`ui/package.json`)

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| next | 14.2.35 | MIT | yes |
| react | 18.3.1 | MIT | yes |
| react-dom | 18.3.1 | MIT | yes |
| zod | 3.23.8 | MIT | yes |

**UI — dev**

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| typescript | 5.9.3 | Apache-2.0 | yes |
| eslint | 8.57.1 | MIT | yes |
| eslint-config-next | 14.2.35 | MIT | yes |
| tailwindcss | 3.4.19 | MIT | yes |
| postcss | 8.5.25 | MIT | yes |
| vitest | 4.1.10 | MIT | yes |
| `@types/{node,react,react-dom}` | — | MIT (DefinitelyTyped) | yes |

**No model weights were added this session**, so no weight licences to record.
That audit starts at Prompt 07 (RIFE) and Prompt 10 (YOLO/Ultralytics, itself
AGPL-3.0).

## Criteria changed

One criterion was **added**, none weakened, none lowered, nothing marked
skip/xfail:

- **New criterion 13 — "build plan not transcribed into tracked files."**
  Justification in *Decisions* above: criterion 11 matches filenames only, and a
  measured transcription passed it. Strengthens the gate (12 → 13 criteria).

`verify_00.sh` was not touched. Criterion 5's torch-blocked CPU assertion is
unchanged — amendment 003 records that it was already correct, so the torch
deferral required no gate edit.

## Gate status

`bash scripts/verify_01.sh` → **PASSED: 13 of 13**, exit 0.

| # | Criterion | Result | Measured |
|---|---|---|---|
| 1 | scaffold files present | PASS | 7/7 |
| 2 | ruff + ruff-format + mypy clean | PASS | 3/3 exit 0 |
| 3 | pytest `-m "not gpu"` green | PASS | 12 passed |
| 4 | `/health` 200, all 10 fields type-valid | PASS | http=200 device=cpu fields=10/10 |
| 5 | CPU fallback: torch blocked → 200, cuda=false, device=cpu | PASS | http=200 device=cpu fields=10/10 |
| 6 | UI tsc + eslint + vitest | PASS | 3/3 exit 0 |
| 7 | `next build` | PASS | 17s |
| 8 | no `any` in `ui/**/*.{ts,tsx}` | PASS | 0 hits |
| 9 | `check_env.py` parseable, exit 0 or 1 | PASS | exit=1, rows=15 |
| 10 | no `print(` in `engine/repcut` | PASS | 0 calls, 6 files |
| 11 | no forbidden files tracked | PASS | clean |
| 12 | `verify_00.sh` no regression | PASS | 13 of 13 |
| 13 | build plan not transcribed (content) | PASS | 0 files |

`bash scripts/verify_00.sh` → **PASSED: 13 of 13**, exit 0. No regression.

### `make test-gpu`

Run locally: **`12 deselected`, 0 collected, exit 0.**

**Zero GPU-marked tests exist, and that is the correct outcome for Prompt 01.**
It is scaffold-only; the sole GPU-touching code is `probes.py`, whose CUDA
branch cannot be exercised without torch (deferred to Prompt 07 — see above) and
whose CPU branch *is* covered, by criterion 5 and by the unit tests. No test was
invented to make this line look better. The first real `@pytest.mark.gpu` tests
arrive with Prompt 07.

The exit-5 tolerance committed in `d48498c` is what makes this report `exit 0`
instead of a spurious failure.

## Open questions for the human

1. **Where does the prompt-tracker feature belong?** It is parked intact
   (7 files + 2 patches). *Recommendation:* give it its own prompt after 02, and
   rewrite `prompts_data.py` to reference prompts by number and read status from
   the filesystem — never copying plan text. As written it cannot be committed.
2. **Disk.** 13GB free against a 20GB threshold, and it fell ~11GB during this
   session alone (`next build` + npm). Prompt 02 ingests 4K footage.
   *Recommendation:* free space before starting Prompt 02.
3. **Concurrent sessions on one working tree.** Two agents writing the same tree
   made this gate unclosable until the tree was quiesced.
   *Recommendation:* one session per working tree, or use git worktrees.

## Risks / known gaps

- **The content guard is a heuristic, not a proof.** It keys on wave-title
  density. A transcription that omits wave titles — or paraphrases them — passes.
  It closes the observed hole and raises the bar; it does not make the class
  impossible. The durable fix is cultural: reference prompts by number.
- **`check_env.py` hard-FAILs on disk and `.env`, but criterion 9 accepts exit
  0 or 1.** Deliberate — GPU and disk state must not block a scaffold gate. The
  consequence is that a genuine environment regression shows as a WARN-coloured
  PASS at the gate. Anyone reading only the gate output will miss it; the disk
  failure above is a live example.
- **The CUDA branch of `probes.py` has never executed.** CI has no GPU and torch
  is not installed, so `cuda_available: true`, `gpu_name` and `vram_*` are
  untested paths. Prompt 07 is the first run that exercises them, and it inherits
  that criterion.
- **`/health` probes FFmpeg on every request** — a `subprocess` call per hit, no
  caching, 10s timeout. Fine for a status page polled by one user; it would be a
  problem if anything hot ever calls `/health`.
- **`dev.sh`'s `taskkill` path is Git-Bash-specific.** It depends on
  `/proc/<pid>/winpid`. On WSL or Linux it falls through to `kill -TERM`, which
  is correct there, but the Windows path has only been exercised on this machine.
  The `$!` bug above means Ctrl-C cleanup was never actually working; the fix is
  correct by construction and syntax-checked, but has not been exercised through
  a real interactive Ctrl-C. Worth confirming on the first `make dev`.
- **No E2E test yet.** Playwright arrives at Prompt 12; nothing currently proves
  the status page renders against a live engine end-to-end — criterion 6 type-
  checks and unit-tests it, criterion 7 builds it, but the two halves are never
  run against each other in CI.
