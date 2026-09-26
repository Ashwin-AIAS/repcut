# Repcut — chat handoff context

One Claude chat per prompt. Paste the **Current block** at the top of a new chat
to start the next prompt's thinking session. Update this file once per gate —
it takes two minutes and saves re-deriving state every time.

This is context for the *thinking layer* (planning, review, debugging). It is
not the Claude Code prompt itself; that gets written inside the chat.

---

## CURRENT BLOCK — paste this to start the Prompt 04 chat

```text
Repcut status handoff. Read this before responding.

POSITION
- Prompt 03 is gated and closed: PR #9 merged to main, tagged `prompt-03-done`,
  docs/reports/prompt-03.md written. `make verify-03`: all 17 automated
  criteria PASS, criterion 16 SKIPs for a structural reason (below), criterion
  19's human checklist signed by hand 2026-09-26.
- Prompt 04 is next, and it is a HUMAN REVIEW taste checkpoint. It also
  inherits two blocking boxes from Prompt 03 — see OPEN ISSUES.
- main HEAD: __MAIN_SHA__. Docs-only commits may land after this; run
  `git log -1 --oneline main` for the current tip.

WHAT EXISTS IN THE REPO
Carried from Prompts 00-02: the .claude/ harness; engine/ with config,
structlog JSON logging, /health, the six-table async schema + alembic,
media/store.py as the ONLY path builder, media/ffmpeg_builder.py as the ONLY
place an FFmpeg or ffprobe argv is built, metadata/artifacts/ingest, api/
(errors, schemas, projects, uploads, jobs, media), the serial in-process job
worker with a WebSocket event stream, security.py's network boundary,
loop.py + `python -m repcut` as the single entry point, redaction.py; ui/ with
the design tokens, the primitives, the Workspace editor shell, Zod mirrors of
every engine model, the chunked uploader and useJobStream.

Prompt 03 added, engine side:
- analysis/ — the package. params.py/types.py (SCENE_PARAMS_VERSION,
  FRAME_PARAMS_VERSION, SceneBoundary, EnergyMeasurement); scenes.py
  (PySceneDetect ContentDetector, run against the proxy); sampler.py (pick_frame
  — sharpest of three candidates by Laplacian variance); motion.py
  (compute_scene_energy — Farneback optical flow plus audio RMS via astats);
  gemini_client.py + cache.py (httpx to the REST endpoint, cache-first lookup,
  token-bucket limiter with RPM in memory and the daily counter persisted to
  $DATA_DIR, capped backoff with jitter, one retry on malformed JSON then None);
  pipeline.py (run_analysis, the JobType.ANALYSIS handler, five resumable
  idempotent stages).
- The Gemini model is PINNED: GEMINI_MODEL = "gemini-3.5-flash",
  GEMINI_PROMPT_VERSION = 2. Never an alias like gemini-flash-latest — an alias
  can move to a worse tier with no changelog. A model swap ships a prompt
  version bump in the SAME commit, or a stale answer from a dead model reads
  back as a cache hit.
- ffmpeg_builder.build_frame_extraction — reads the SOURCE, not the proxy;
  conditionally HDR tone-maps (zscale+tonemap, probed once per clip); strips
  metadata with -map_metadata -1. metadata.parse_color_properties came with it.
- Schema: Scene (keyed (sha256, detector_params_version, sequence_index)) and
  GeminiSceneCache (keyed (scene_id, gemini_prompt_version)), migration 0002.
- Routes: GET /media/{sha256}/scenes and
  GET /media/{sha256}/scenes/{scene_id}/frame (Range-aware).
- Analysis auto-enqueues after a successful ingest, behind an
  _analysis_complete check — the unconditional version broke two already-shipped
  Prompt 02 invariants (a duplicate upload must enqueue zero jobs; a fresh
  upload's job list is exactly one ingest job).

Prompt 03 added, UI side:
- components/analysis/ — SceneStrip (per-scene tags, three states collapsed
  from the API's single `vlm: null`), EnergySparkline, PrivacyDisclosure (renders
  on the "sending scene N of M to Gemini" job step — the P4 disclosure, live,
  not buried in settings), AnalysisPanel wiring them into Workspace as a panel
  that only renders once a clip has scenes.

Prompt 03 added, tooling:
- scripts/verify_03.sh + verify_03_checks.py — the 19-criterion gate.
- A root-level pyproject.toml carrying just [tool.ruff], so scripts/ is linted
  (S ruleset included). engine/'s own pyproject is also that package's build
  config, which is why there are two and not one.
- conftest.py fixtures: an HDR-tagged clip and a motion/loudness-step clip.
- 443 engine tests (up from 291 at the Prompt 02 merge).

AMENDMENTS IN FORCE (docs/guide-amendments/)
- 000 — the Prompt 00 agent harness exists; not in the original guide. ACCEPTED
- 001 — CI jobs gated on scaffolding presence. ACCEPTED
- 002 — Prompt 01 scope reduced; Prompt 00 had already delivered the scaffold.
  ACCEPTED
- 003 — torch DEFERRED to Prompt 07. The guide's "status page shows CUDA true"
  criterion moves to Prompt 07's gate. ACCEPTED
- 004 — Prompt 02: synthetic fixtures plus a human checklist instead of
  committed footage; ffmpeg_builder at engine/repcut/media/; a content-addressed
  store; the 2GB memory test slow-marked and disk-gated; the two-track split;
  refcounting and orphan GC deferred to Prompt 12; SKIP added as a third gate
  verdict. ACCEPTED
- 005 — the guide had NO security content; a security model added as §7. The
  repo's rules were ahead of the guide. ACCEPTED
- 006 — the build plan is never transcribed into the repo IN ANY FORM: not as
  data, a fixture, a docstring or prose. Titles, summaries, deliverables, wave
  structure and calendar estimates ARE the plan. verify-01 criterion 13 matches
  content not filenames; verify-02 criterion 22 catches a single title and
  SKIPs where the guide is absent (CI has no guide). ACCEPTED
- 007 — the Next.js 14→16 upgrade. Approved and shipped at the Prompt 02 gate,
  written up only in Prompt 03. Paper-only; no code change. ACCEPTED
- 008 — Prompt 03's six collisions between the guide's text and this repo:
  package path, frame storage, frame source, boundary timebase, fixtures,
  detection input. ACCEPTED
- 009 — Prompt 03 criterion 15 rewritten from "no noqa" to "no UNJUSTIFIED
  noqa". A blanket ban would have been satisfied by deleting the directives
  rather than by reading them. ACCEPTED
- 010 — gemini-2.0-flash was RETIRED by the provider mid-prompt. The model is
  now pinned to gemini-3.5-flash with GEMINI_PROMPT_VERSION bumped to 2 in the
  same commit. Deliberately not an alias. ACCEPTED
- 011 — real-HDR verification MOVES from Prompt 03's manual check to Prompt 04's
  taste gate. The local footage library cannot sign it honestly: exactly one
  real HDR clip, two clips with no moov atom at all, and the rest
  WhatsApp-compressed h264/bt709 with no HDR to judge. Prompt 03's automated
  criteria 2 and 11 still cover the synthetic fixture. ACCEPTED

STANDING CONSTRAINTS BEYOND CLAUDE.md
- Do NOT install torch/torchvision/torchaudio until Prompt 07 (amendment 003).
- make is GNU Make 3.81 (mingw32, 2006). No `.ONESHELL` — chain recipe steps
  with `&&`. Run make from Git Bash, not PowerShell.
- On Windows a bare `bash` is WSL: CreateProcess searches System32 before PATH,
  and System32's bash.exe is WSL's launcher. The stack half-works under it —
  servers start on the host, but every observation the script makes about them
  is wrong. scripts/posix_shell.py exists for this; no recipe may spawn a bare
  shell.
- The engine boots correctly ONLY through `python -m repcut`. A hand-written
  `uvicorn --reload` line selects an event loop with no subprocess transport and
  every FFmpeg call dies.
- A sandboxed shell has no console (GetConsoleWindow() == 0), so it cannot
  deliver CTRL_C_EVENT. Any Ctrl-C criterion SKIPs there and must be run from
  cmd.exe or PowerShell to go green. This is why verify-03's criterion 16 SKIPs.
- Long gate orchestration runs have been killed partway through by this
  environment, with no test failure ever appearing. Run criteria individually
  when that happens, and say so in the report rather than claiming the single
  run completed.
- $DATA_DIR must sit OUTSIDE any cloud-sync folder (amendment 004). The music
  library is $DATA_DIR/music/, not in the repo.
- Branch `project-process-dashboard` is off-plan personal work, pushed but not
  merged. Do not touch it in prompt sessions. Its open fix: prompts_data.py
  hardcodes the roadmap into a public repo, which amendment 006 forbids.
- The repo is PUBLIC. .claude/rules/secrets.md is absolute.

OPEN ISSUES / DEBT
Not "none". Two of Prompt 02's eight are now fixed — scripts/ is linted
(issue 5) and `make dev` returns 130 on Ctrl-C instead of a traceback
(issue 7) — and issue 6, the never-observed live jobs panel, was observed
filling in during Prompt 03's real-footage check. What remains:

1. BLOCKING PROMPT 04 — the proxy does not tone-map HDR. Real phone source is
   HEVC Main 10, BT.2020 primaries, HLG transfer, with a Dolby Vision RPU.
   `scale` converts the matrix and cannot convert primaries or transfer, so
   those flags are dropped without a warning and the proxy is untone-mapped HDR
   that no browser maps. The preview is washed out and its colour triple
   describes no real colour space. A grade judged against this would be tuned to
   cancel out a bug. Read docs/future-prompts/prompt-04-colour-baseline.md.
2. BLOCKING PROMPT 04 — docs/manual-checks/prompt-04.md carries the two boxes
   amendment 011 migrated out of Prompt 03: a real HDR/HEVC clip analysed, and
   the sampled frame not washed out. Prompt 03 shipped without anyone having
   seen a tone-mapped frame from real HDR footage. They are blocking checks on
   04's own gate. The footage library problem is real — one usable HDR clip —
   so shooting a fresh HDR clip is probably a prerequisite, not an afterthought.
3. The proxy caps the wrong axis. ProxyRecipe caps HEIGHT at 720, so portrait
   source (2160x3840 display) yields a 406x720 preview — the budget is spent on
   the axis the user has to spare. A params_version bump plus a re-encode of
   everything ingested, so it is Prompt 05 territory. Note the interaction with
   issue 1: both are the proxy, and doing them in one re-encode is cheaper than
   two.
4. FOR PROMPT 05's CUT PLANNER — a sub-second trailing scene at the end of a
   recording (one test clip has 3:11-3:12, the camera being lowered) is a
   CORRECT detection, not an artifact, and "transition" is a fair tag. Do not
   tune detection to suppress it. But it consumes a Gemini call and becomes a
   unit of work downstream, so the cut planner should recognise
   end-of-recording scenes and drop them rather than treat them as usable
   footage.
5. FOR EVERY PROMPT THAT READS SCENE TAGS — one sampled frame per scene means a
   long scene's tag describes an instant, not the scene. One real clip's scene 1
   is 3m11s. This is correct by the P4 boundary and sending more frames would
   breach it; the risk is downstream code reading a point sample as a span
   description. Treat a long scene's tag as low-confidence about its whole
   length, or split the scene — never assume it characterises three minutes.
6. The P4 disclosure is a job-progress step, not a persistent notice. It
   satisfies "disclose at the moment it happens" literally, but a user not
   watching the jobs panel at that moment misses it. An observation, not a
   defect. Also worth knowing for the next real-footage check: a clip the store
   has already seen dedupes, and a dedupe hit sends nothing to Gemini, so there
   is nothing to disclose — you need at least one genuinely new clip.
7. verify-02 criterion 13 (2GB upload, peak RSS) passed on the prompt-03 branch
   at 359MB and was NOT RE-RUN after the later commits, none of which touch the
   upload path. "Not re-run", not "omitted".
8. Refcounting and orphan GC are deferred to Prompt 12 (amendment 004). The
   deferral ends EARLY if any prompt before 12 ships a delete or remove surface.
   Nothing can be orphaned yet.
9. The loop guarantee is bypassable via the entry point — a startup warning plus
   a named 503, deliberately, because the UI needs a reachable engine to render
   the gap.
10. Two smaller ones from Prompt 02, still open: UnexpectedErrorBoundary
    re-raises once a response has started, so uvicorn's own logger prints an
    unredacted traceback to the console (no response body is affected); and a
    cancelled job has no UI state distinct from a failure.

BUILDER CONTEXT
Ashwin, ~5 hrs/week, €0 budget, RTX 3050 (4GB VRAM) laptop. Prefer the smallest
correct step over the impressive one. Claude Code executes the build prompts
autonomously; this chat is the thinking layer.

PROCESS (standing, since the Prompt 02 gate)
- Session reports are capped at roughly two pages: decisions and open issues
  only. Prompt 02's ran to 1,237 lines and the ratio had drifted. Prompt 03's
  still overran; the cap is a real target, not a suggestion.
- Claude Code runs the gate loop itself via docs/prompts/autonomous-loop.md
  rather than relaying each iteration through this chat. Human criteria stay
  outside the loop — an agent may never tick a box in docs/manual-checks/.
- Every prompt owes at least one criterion that starts the product the way a
  person starts it, and asserts something a person would notice.

WHAT I WANT FROM THIS CHAT
Prompt 04 — the colour work, and the first taste checkpoint. Before anything
else read docs/future-prompts/prompt-04-colour-baseline.md and
docs/manual-checks/prompt-04.md: between them they say that the preview this
prompt would grade against is itself broken, and that two HDR boxes now block
04's gate. The order question worth settling in this chat, before any kick-off
prompt is written: does the proxy's colour pipeline get fixed first, so there is
an honest baseline to judge a grade against, and does that fix pull Prompt 05's
wrong-axis re-encode forward into the same params_version bump?

A gate can prove the code runs; it cannot tell me the edit looks good. That is
what this checkpoint is for, so help me decide what I am looking at before I
look at it.

Start by confirming you have the guide's Prompt 04 section, then help me with
[plan review / kick-off prompt / session report review / debugging].
```

---

## REUSABLE TEMPLATE — for Prompt 04 and beyond

Copy, fill the bracketed parts, paste into a fresh chat.

```text
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

PROCESS (standing, since the Prompt 02 gate)
- Session reports are capped at roughly two pages: decisions and open issues
  only. Prompt 02's ran to 1,237 lines and the ratio had drifted.
- Claude Code runs the gate loop itself via docs/prompts/autonomous-loop.md,
  rather than relaying each iteration through this chat. Human criteria stay
  outside the loop — an agent may never tick a box in docs/manual-checks/.

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
