# Repcut — chat handoff context

One Claude chat per prompt. Paste the **Current block** at the top of a new chat
to start the next prompt's thinking session. Update this file once per gate —
it takes two minutes and saves re-deriving state every time.

This is context for the *thinking layer* (planning, review, debugging). It is
not the Claude Code prompt itself; that gets written inside the chat.

---

## CURRENT BLOCK — paste this to start the Prompt 03 chat

```
Repcut status handoff. Read this before responding.

POSITION
- Prompt 02 is gated and closed: PR #4 merged to main, tagged `prompt-02-done`,
  docs/reports/prompt-02.md written. `make verify-02` PASSED 27 of 27 —
  criterion 16, the real-phone-footage human check, signed 2026-08-25.
- Wave 1 continues. Prompt 03 is next.
- main HEAD: 4a506a4. Working tree clean.

WHAT EXISTS IN THE REPO
Carried from Prompt 01: the .claude/ harness, engine/ (config, structlog JSON
logging, /health), ui/ (App Router, Zod-parsed /status), scripts/ (check_env,
dev.sh, setup.sh, verify_00/01), the Makefile, three CI workflows.

Prompt 02 added, engine side:
- db/ — six tables as SQLAlchemy 2 async models, a UTC-enforcing column type,
  the constraint naming convention, the async session factory; alembic/ holds
  the migration and db/migrations.py applies it at startup.
- media/store.py — the ONLY path builder. Every path is $DATA_DIR-relative, no
  component derives from user input, and absolute() refuses anything that
  resolves outside $DATA_DIR.
- media/ffmpeg_builder.py — every FFmpeg and ffprobe argv in the project: the
  probe, the 720p CFR proxy, the tiled thumbnail strip, a two-second dry run,
  typed errors classified from stderr, and an async runner that writes to a
  temp name and moves it into place.
- media/metadata.py — one ffprobe document into stored properties. Owns
  rotation, source audio rate, and a three-valued VFR answer.
- media/artifacts.py — artifact kinds, recipe parameters, and the
  PARAMS_VERSION table that keys derived artifacts. Changing a recipe means
  bumping it and re-encoding what exists.
- media/ingest.py — probe, thumbnail strip, proxy; keyed and skipped by
  (sha256, kind, params_version).
- api/ — errors.py (named errors, one renderer, an outermost error boundary),
  schemas.py, deps.py, projects.py, uploads.py (chunked resumable transfer),
  jobs.py (/jobs, /ws/jobs), media.py (proxy and strip, with Range support).
- jobs.py — the in-process serial job worker, its event stream, monotonic
  progress, and cancel.
- security.py — the network boundary: TrustedHost allow-list, explicit CORS
  origins with allow_credentials=False, per-route WebSocket Origin checks
  before accept(), loopback bind.
- loop.py + __main__.py — the event loop the engine requires, and
  `python -m repcut`, the single entry point every launcher goes through.
- redaction.py — redact_paths, shared by the error renderer and FFmpeg logging.

Prompt 02 added, UI side:
- app/globals.css + tailwind.config.ts — the design tokens, and Tailwind bound
  to them. They are the only source of style; a hex anywhere else fails a gate.
- app/fonts/ — Sora and IBM Plex Sans, SIL OFL 1.1, via next/font/local.
- components/primitives/ — Button, Badge, Panel, Progress, Skeleton, Slider,
  Modal, AiSuggested. No className escape hatch on any of them.
- components/ — Dropzone, UploadQueue, MediaCard, ProxyPlayer, JobList,
  NewProject, EngineDown, and Workspace, the editor shell. The shell draws
  topbar, media library, preview, transfers and jobs. There is deliberately NO
  inspector and NO timeline yet — they arrive with their content, in 03+.
- lib/api/ — engine.ts, schemas.ts (Zod mirrors of the engine's models),
  client.ts (browser calls, every result discriminated, never a throw),
  server.ts (server-only, first-paint reads).
- lib/upload.ts — the chunked uploader: File.slice, a hash-wasm incremental
  digest, and resume-by-hash before a transfer is opened.
- lib/jobs/useJobStream.ts — /ws/jobs with reconnect and backoff.

Prompt 02 added, tooling:
- scripts/verify_02.sh + verify_02_checks.py — the 27-criterion gate.
- scripts/posix_shell.py — resolves a real POSIX shell, never a bare `bash`.
- scripts/dev_stack.py + dev.sh — the `make dev` launcher, with port hygiene.
- scripts/cdp_browser.py — a minimal CDP client, for the browser criterion.
- scripts/check_plan_titles.py — criterion 22, the single-title plan-leak check.
- 291 engine tests and 183 UI tests, all CPU.

AMENDMENTS IN FORCE (docs/guide-amendments/)
- 000 — Prompt 00 agent harness exists; not in the original guide. ACCEPTED
- 001 — CI jobs gated on scaffolding presence. ACCEPTED
- 002 — Prompt 01 scope reduced; Prompt 00 had already delivered the scaffold.
  ACCEPTED
- 003 — torch DEFERRED to Prompt 07. The guide's "status page shows CUDA true"
  criterion moves to Prompt 07's gate. ACCEPTED
- 004 — Prompt 02: synthetic fixtures plus a human checklist instead of
  committed footage; ffmpeg_builder lives at engine/repcut/media/; a
  content-addressed media store; the 2GB memory test is slow-marked and
  disk-gated; the two-track split; refcounting and orphan GC deferred to
  Prompt 12; SKIP added as a third gate verdict. ACCEPTED
- 005 — the guide had NO security content of any kind; a security model added
  as §7. The repo's rules were ahead of the guide. ACCEPTED
- 006 — the build plan is never transcribed into the repo IN ANY FORM: not as
  data, a fixture, a docstring or prose. Prompt titles, summaries,
  deliverables, wave structure and calendar estimates ARE the plan. verify-01
  criterion 13 matches content rather than filenames; verify-02 criterion 22
  catches a single title and SKIPs where the guide is absent (CI has no guide).
  ACCEPTED
- UNNUMBERED AND OWED — the next 14 to 16 upgrade. A major framework bump:
  14.2.35 is the end of its line, six high-severity advisories, no patch
  coming. Ashwin approved it before it was made and it is documented in
  docs/reports/security-review-2026-08-07.md, but it was never written as an
  amendment, and CLAUDE.md went on listing Next.js 14 as the approved stack.
  That line was fixed at the Prompt 02 gate; the amendment document is still
  missing. Either write it as 007, or record deliberately that a security
  upgrade inside an already-approved framework does not need one. React stayed
  on 18, to keep the blast radius to the framework itself.

STANDING CONSTRAINTS BEYOND CLAUDE.md
- Do NOT install torch/torchvision/torchaudio until Prompt 07 (amendment 003).
- make is GNU Make 3.81 (mingw32, 2006). No `.ONESHELL` — chain recipe steps
  with `&&`. Run make from Git Bash, not PowerShell.
- On Windows a bare `bash` is WSL: CreateProcess searches System32 before PATH,
  and System32\bash.exe is WSL's launcher. The stack half-works under it —
  servers start on the host, but every observation the script makes about them
  is wrong. scripts/posix_shell.py exists for this, and no recipe may spawn a
  bare shell.
- The engine boots correctly ONLY through `python -m repcut`. A hand-written
  `uvicorn --reload` line selects an event loop with no subprocess transport,
  and every FFmpeg call dies. The engine warns and /health reports it, but the
  guarantee lives in the entry point.
- $DATA_DIR must sit OUTSIDE any cloud-sync folder (amendment 004). The music
  library is $DATA_DIR/music/, not in the repo.
- Branch `project-process-dashboard` is off-plan personal work, pushed but not
  merged. Do not touch it in prompt sessions. Its open fix: prompts_data.py
  hardcodes the roadmap into a public repo, which amendment 006 forbids.
- The repo is PUBLIC. .claude/rules/secrets.md is absolute.

OPEN ISSUES / DEBT
Not "none" — seven, all recorded in docs/reports/prompt-02.md under Open issues.

1. The proxy caps the wrong axis. ProxyRecipe caps HEIGHT at 720, so portrait
   phone source (2160x3840 display) yields a 406x720 preview: the budget is
   spent on the axis the user has to spare. Fixing it is a params_version bump
   plus a re-encode of everything ingested, so it is Prompt 05 territory.
   PROMPT 03 MUST NOT SAMPLE FRAMES FROM THE PROXY — see
   docs/future-prompts/prompt-03-frame-source.md, which names the assertion
   03's gate owes: the sampled frame's dimensions must equal the SOURCE's
   display dimensions, read from media_blobs.
2. The proxy does not tone-map HDR. Real phone source is HEVC Main 10, BT.2020
   primaries, HLG transfer, with a Dolby Vision RPU. `scale` converts the
   matrix and cannot convert primaries or transfer, so those two flags are
   dropped without a warning and the proxy is untone-mapped HDR that no browser
   maps. The preview is washed out and its colour triple describes no real
   colour space. This blocks Prompt 04's taste work — a grade judged against it
   would be tuned to cancel out a bug. See
   docs/future-prompts/prompt-04-colour-baseline.md.
3. Refcounting and orphan GC are deferred to Prompt 12 (amendment 004). The
   deferral ends EARLY if any prompt before 12 ships a delete or remove
   surface — a "remove clip", a project delete, an export cleanup. Prompt 02
   ships no delete endpoint, so nothing can be orphaned yet.
4. The loop guarantee is bypassable via the entry point. It is a startup
   warning plus a named 503, not a refusal, deliberately — the UI needs a
   reachable engine to render the gap. Criterion 17 asserts dev.sh has not been
   edited back; nothing can stop a hand-written uvicorn line.
5. scripts/ is linted by nothing. `make lint`, CI and the pre-commit hooks are
   all scoped to engine/. NOTE, because an earlier draft of this said the
   opposite: ruff's S (flake8-bandit) ruleset IS enabled in engine/ — a planted
   shell=True raises S602 — and the security review's "zero pre-existing S
   findings" was a real scan. RUF100's "non-enabled: S603" means that rule sits
   in the ignore list, not that bandit is off. What has never been scanned is
   scripts/, which is exactly where the process-spawning code lives
   (posix_shell.py, dev_stack.py, cdp_browser.py) and which carries three dead
   `# noqa: S603` directives, written against a scanner that never looked.
   security.md's "Only S603/S607 are globally ignored" is the sentence to fix.
6. The live jobs panel has never been observed updating. The socket connects —
   criterion 20 asserts /ws/jobs is accepted against a real `make dev` stack
   and the panel reports connected — but every clip in the real-footage session
   was already ingested, so each upload took the duplicate path and no job was
   ever queued. Connection verified, live fill-in unobserved. Prompt 03's
   Playwright layer is where the assertion belongs.
7. `make dev` ends a Ctrl-C with a raw Python traceback: KeyboardInterrupt
   through the subprocess.call in scripts/posix_shell.py, uncaught. The
   teardown itself is correct and criterion 19 asserts it; the surface is
   wrong. Wants an `except KeyboardInterrupt: return 130`.

Two smaller ones, also in the report: UnexpectedErrorBoundary re-raises once a
response has started, so uvicorn's own logger prints an unredacted traceback to
the console (no response body is affected); and a cancelled job has no UI state
distinct from a failure.

Fixed AT the gate, and worth carrying as history: CI's `Dependency advisories`
job had never audited a single package. `pip-audit --strict` died on
repcut-engine itself, which is not on PyPI, and had done so since the day the
job was added. It now audits the resolved dependency set from a throwaway venv,
still --strict, and its first real run is clean.

BUILDER CONTEXT
Ashwin, ~5 hrs/week, €0 budget, RTX 3050 (4GB VRAM) laptop. Prefer the smallest
correct step over the impressive one. Claude Code executes the build prompts
autonomously; this chat is the thinking layer.

WHAT I WANT FROM THIS CHAT
Prompt 03. Before anything else, read
docs/future-prompts/prompt-03-frame-source.md — written during Prompt 02 while
the measurement was fresh. It names the trap Prompt 03 walks into: there are
two files per clip, the proxy is the convenient one and the wrong one, and
sampling from it sends Gemini a thumbnail of a 4K frame with nothing erroring
anywhere. It also names what 03's gate must assert, and two related findings
(the HDR conversion, and stripping metadata before upload).

Carry in as well: the P4 boundary is one sampled frame per scene and nothing
else, ever; the Gemini cache key is (video_hash, scene_id, prompt_version) and
a cache miss on a repeat run is a bug; and the rule this prompt earned — every
prompt from here owes at least one criterion that starts the product the way a
person starts it, and asserts something a person would notice.

Start by confirming you have the guide's Prompt 03 section, then help me with
[plan review / kick-off prompt / session report review / debugging].
```

---

## REUSABLE TEMPLATE — for Prompt 04 and beyond

Copy, fill the bracketed parts, paste into a fresh chat.

```
Repcut status handoff. Read this before responding.

POSITION
- Prompt [NN-1] is gated and closed: merged to main, tagged `prompt-[NN-1]-done`,
  docs/reports/prompt-[NN-1].md written.
- Wave [X], Prompt [NN] is next.
- main HEAD: [sha]. Working tree [clean / has: ...].

WHAT EXISTS IN THE REPO
[Append what the last prompt added. Keep it to modules and their purpose, not
file-by-file — the chat can read the repo.]

AMENDMENTS IN FORCE
[List every amendment number, one line each, with its effect. This is the part
most easily forgotten and most expensive to lose.]

STANDING CONSTRAINTS BEYOND CLAUDE.md
[Carry forward: torch deferral until it lands, make 3.81, the dashboard branch,
plus anything new — pinned model versions, VRAM findings, quota limits hit.]

OPEN ISSUES / DEBT
[Anything a gate passed *around* rather than through. Deferred criteria,
skipped manual checklists, known-flaky tests. If this section is empty, say
"none" explicitly rather than omitting it.]

BUILDER CONTEXT
Ashwin, ~5 hrs/week, €0 budget, RTX 3050 (4GB VRAM) laptop. Prefer the smallest
correct step. Claude Code executes; this chat is the thinking layer.

WHAT I WANT FROM THIS CHAT
Prompt [NN] — [title]. [Known conflicts with the rules, if any.]
Start by confirming you have the guide's Prompt [NN] section, then
[write the Claude Code kick-off / review this session report / debug X].
```

---

## Updating this file

After each `/gate NN`, in the same session or the next chat:

1. Move POSITION forward one prompt; update the main HEAD sha.
2. Append what the prompt added to WHAT EXISTS.
3. Add any new amendment to AMENDMENTS IN FORCE.
4. Add anything the gate deferred, skipped or worked around to OPEN ISSUES.
5. Rewrite WHAT I WANT for the next prompt.

The section that matters most is **AMENDMENTS IN FORCE**. Every one of them is
a place where the repo and the build guide disagree, and a fresh chat that
doesn't know about them will confidently recommend the guide's version.

## The five human review gates

Prompts 04, 05, 06, 08, 10 need a taste checkpoint, not just a green gate. Give
those a chat of their own, before running the prompt, to work out what you're
looking for — grades, cut feel, reference match. A gate can prove the code
runs; it cannot tell you the edit looks good.
