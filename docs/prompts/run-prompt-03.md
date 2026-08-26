# Prompt 03 kick-off

Paste the PROMPT block below into Claude Code, in the repo folder, after
`/run-prompt 03`.

**This file deliberately does not restate the guide's deliverables.**
`/run-prompt 03` reads them from `$REPCUT_GUIDE_PATH` itself, and amendment 006
forbids transcribing them here in any form. What follows is only the part the
guide does not contain: the six places Prompt 03 collides with this repo, the
one trap it walks into with nothing erroring, and the gate that resolves both.

---

## Why this prompt needs amendments before any code

| # | Conflict | Resolution |
|---|---|---|
| 1 | The guide's package path, against the installed package at `engine/repcut/` | `engine/repcut/analysis/` — the same resolution amendment 004 §2 gave `ffmpeg_builder` |
| 2 | The guide's per-project frames directory, against amendment 004 §6's content-addressed store | A sampled frame is a pure function of (source bytes, recipe): it is a `DerivedArtifact`, keyed `(sha256, artifact_kind, params_version)`, not a project-folder file |
| 3 | Which of the two files per clip a frame is sampled from — the guide is silent | **The source, always.** Asserted by dimension, not by path. See below |
| 4 | The guide stores scene boundaries as frame numbers; there are two files per clip with two different timebases, and one of them is VFR | Boundaries are stored as **seconds** against the source, plus the source frame index. A frame number with no timebase attached is the desync bug in a new costume |
| 5 | The guide's first success criterion needs real gym clips; `testing.md` forbids committing media | The same split amendment 004 §1 made: synthetic fixtures in `verify-03`, plus a human-signed `docs/manual-checks/prompt-03.md` |
| 6 | The guide's runtime budget names a detection resolution but not a detection *input* | Detection may read the proxy — it is a timing decision and the proxy is CFR. Sampling may not. Conflict 4 is what keeps that split honest |

Conflict 3 is the one this prompt exists to get right, and conflict 2 is the one
that costs the most to retrofit, because it is a schema key.

**Do not install torch, torchvision or torchaudio** (amendment 003). Nothing in
this prompt needs them. Optical flow is CPU here.

---

## The trap, stated once

`docs/future-prompts/prompt-03-frame-source.md` is required reading and is not
summarised away here. The short version, because it is the reason for criterion
2:

There are two files for every clip. The proxy is small, local, already decoded
and already the thing the timeline scrubs — and it is **406x720 for 2160x3840
portrait phone source**, because the recipe caps the wrong axis (open issue 1,
Prompt 05 territory). Sampling from it sends Gemini a thumbnail of a 4K frame.

**Nothing errors.** A 406x720 JPEG is a valid image, the API accepts it, the
labels come back well-formed and pass the Pydantic schema. They are just worse.
There is no exception, no malformed response, and no log line that looks wrong.

Two riders from the same measurement:

- **The source is HDR** — HEVC Main 10, BT.2020 primaries, HLG transfer, Dolby
  Vision RPU. A frame extracted without a tone map is washed out whichever file
  it came from, so "sample from the source" is necessary and not sufficient.
  The extraction owns its own colour conversion. Open issue 2 owns the proxy's
  version of this defect; do not fix that one here.
- **Strip the metadata.** These clips carry timed-metadata tracks and
  ambient-viewing-environment side data. `gemini-usage.md` requires EXIF/GPS
  gone before upload; `-map_metadata 0` would carry more than the picture.

---

## PROMPT — Prompt 03

### Role & Context

```
You are a senior ML engineer continuing Repcut from Prompt 02 (merged to main,
tagged prompt-02-done, verify-02 green 27/27 including the human real-footage
check). Build the analysis pipeline: scenes, one sampled frame per scene,
per-scene understanding via Gemini, motion and audio energy.

Read PROMPT 03 in full from $REPCUT_GUIDE_PATH. Then read, in this order:
docs/future-prompts/prompt-03-frame-source.md, docs/guide-amendments/ 003-006,
and the Open issues section of docs/reports/prompt-02.md. Amendments 003, 004
and 006 are in force and change what you may install, where artifacts live, and
what may be written into this repo.

Run this session under docs/prompts/autonomous-loop.md. Plan first, and wait for
approval on the plan; after that, do not come back between gate iterations.
```

### Deliverable 0 — two amendments, before any implementation

Both in the format of 000-006 (What the guide says / What we found / Why the
guide's version doesn't work / Proposed change / Consequences / Principle
check), and both the first commits on `prompt-03`:

- **007 — the Next.js 14 to 16 upgrade.** Owed since 2026-08-07 and never
  written. The upgrade itself was approved before it was made and is documented
  in `docs/reports/security-review-2026-08-07.md`: 14.2.35 is the end of its
  line, six high-severity advisories, no patch coming. React stayed on 18 to
  keep the blast radius to the framework. `CLAUDE.md` went on listing Next.js
  14 as the approved stack until the Prompt 02 gate fixed the line. Write the
  amendment from the security review, not from this file.
- **008 — Prompt 03's six conflicts**, exactly as the table above resolves
  them, with the reasoning derived from the rules files and amendments they
  collide with.

Nothing else starts until both exist: 008 changes the schema and the storage
key, which every prompt from 04 to 13 reads.

### The build deliverables

Read them from the guide; they are not restated here. What follows is only
where this repo's answer overrides the guide's literal text:

- **The analysis package is `engine/repcut/analysis/`.**
- **Frame extraction goes in the existing `ffmpeg_builder.py`.** It is the only
  place in this project an FFmpeg or ffprobe argv is constructed and that does
  not get an exception. The extractor owns the tone map and the metadata strip;
  nothing downstream is allowed to shell out for a frame.
- **`artifacts.py` gains a kind and a recipe.** A sampled frame is a derived
  artifact: new `ArtifactKind`, new frozen recipe dataclass beside the existing
  two, new `PARAMS_VERSION` entry. The recipe carries the tone-map target and
  the candidate count. Changing it means bumping the version in the same
  commit, per that module's docstring.
- **Two new tables**, with an alembic migration that round-trips: scenes, and
  the Gemini response cache whose primary key is
  `(video_hash, scene_id, prompt_version)` per `.claude/skills/gemini-free-tier`.
- **Prefer `httpx` over a Gemini SDK.** It is already a dependency, the request
  is one POST, and an SDK is a new licence to verify plus a telemetry surface to
  audit on a public repo. If you take the SDK anyway, justify it in the report
  and verify its licence per `frontend-and-licensing.md`.
- **New dependencies:** whatever scene detection and optical flow need, each one
  free and AGPL-compatible, each licence verified and recorded. No torch.

### Constraints

Everything in `.claude/rules/` applies. The ones this prompt will actually hit:

- **The P4 boundary is one sampled frame per scene and nothing else, ever.** Not
  a burst for accuracy, not the audio, not the filename, not the path. A request
  to send more is a P1-P5 conflict: stop and ask. The gate counts what crosses
  the transport seam, not what the call site intended.
- **A cache miss on a repeat run is a bug**, not a warning. Check the cache
  before every call, unconditionally.
- **The rate limiter fails closed.** Token bucket against `GEMINI_RPM_LIMIT` and
  `GEMINI_DAILY_LIMIT`, enforced before the request. A caught 429 is not flow
  control — by then the call is spent.
- **Degrade, never crash.** Malformed JSON gets one retry with a JSON-only
  reinforcement, then `vlm: null`. Network down, quota gone, key absent: the
  pipeline completes, local features still populate, the UI says why.
- **`GEMINI_API_KEY` never appears** in a log, a report, a fixture, an error
  message or a test. `config.py` already exposes only `gemini_api_key_set`;
  keep it that way.
- **The UI discloses the send at the moment it happens**, not in a settings page.
- **Proxies stay preview-only.** Detection may read one (conflict 6). Nothing
  that reaches Gemini may.
- **Named exceptions, no bare `except Exception:`,** no raw traceback to the UI,
  no silently stuck job. Analysis is a resumable job on the existing worker with
  per-stage progress, not a new parallel mechanism.
- **make is GNU Make 3.81** — no `.ONESHELL`, chain with `&&`. No recipe spawns
  a bare `bash`; go through `scripts/posix_shell.py`.
- **Gemini is mocked in every test.** Zero live API calls in the suite, ever.

### Two debt items folded in

Both are small, both are in the way, and both are things a person notices:

- **`scripts/` is linted by nothing** (open issue 5). `make lint`, CI and the
  pre-commit hooks are all scoped to `engine/`, and `scripts/` is exactly where
  the process-spawning code lives — it carries three dead `# noqa: S603`
  directives written against a scanner that never looked. Bring `scripts/` under
  ruff with the same ruleset. Fix or justify every finding in the report; do
  **not** add an ignore entry, and fix the sentence in `security.md` that claims
  the coverage already existed.
- **`make dev` ends a Ctrl-C with a raw traceback** (open issue 7):
  `KeyboardInterrupt` through the `subprocess.call` in `scripts/posix_shell.py`,
  uncaught. The teardown is correct; only the surface is wrong. Wants an
  `except KeyboardInterrupt: return 130`.

### Sequencing

One branch, `prompt-03`, two tracks with a hard checkpoint between them:

1. **Track A — engine.** Amendments 007 and 008 -> migration -> frame extractor
   and its argv snapshots -> scenes -> sampler -> Gemini client (cache, limiter,
   backoff, schema) -> motion -> pipeline job. Run `/checkpoint` here. If the
   session ends, this is a coherent place to stop.
2. **Track B — UI.** The per-clip analysis view, the disclosure, and the
   degradation state.

Delegate Track A to `gemini-steward` and `video-pipeline-engineer`, the schema
to `engine-architect`, Track B to `frontend-engineer`. Author the gate with
`gate-runner` as you go, not at the end.

### Autonomy Protocol

Fully autonomous, under `docs/prompts/autonomous-loop.md`. The Gemini prompt
wording is yours to engineer — include it verbatim in the report. Decide
implementation details, defaults (record under "Assumed"), and bug fixes
anywhere you find them.

Stop and ask only for: P1-P5 conflicts, anything paid, contradictory
requirements, destructive actions outside the repo, anything touching a
credential. Prompt 03 is **not** a human-review checkpoint — but criterion 19
is `[HUMAN]` and no agent may tick it.

### Success Criteria (= `make verify-03`)

`scripts/verify_03.sh`, same contract as `verify_00`-`verify_02`: binary,
exit-coded, per-criterion, idempotent, prints the **measured value** next to
each verdict. `SKIP` is a verdict, with a reason. See
`.claude/skills/verify-gate-authoring`.

All fixtures generated at test time by the existing `conftest.py` factory. No
media committed — including the HDR fixture, which is `lavfi` plus a
`setparams` colour tag, not a real clip.

1. **Migrations round-trip.** `upgrade head` / `downgrade base` / `upgrade head`
   on a scratch DB, exit 0 each time. Both new tables present, with the cache's
   three-column primary key.
2. **The sampled frame comes from the source.** For a rotated portrait fixture,
   the sampled frame's width and height **equal `media_blobs.display_width` and
   `display_height`** — not the proxy's, and not the source's coded dimensions,
   which are landscape for rotated phone video. Print all three pairs.
   **Assert the dimensions of the frame that was sampled, never the path it was
   read from:** a path assertion passes the moment someone adds a resize.
3. **Exactly one frame per scene leaves the machine.** Counted at the transport
   seam with a mocked transport: N detected scenes produce N outbound image
   parts, no audio part, no second frame, and no filename or path anywhere in
   the request body. Print scenes and parts.
4. **A repeat run costs zero API calls.** Run the pipeline twice; the second run
   makes 0 requests and logs a cache hit per scene. Print requests on run 1 and
   run 2.
5. **`prompt_version` invalidates deliberately.** Bump it, re-run, and the calls
   come back — proving the key works rather than that caching is unconditional.
6. **The limiter fails closed.** With the bucket exhausted, no request is made:
   asserted against the transport, not against a log line. Backoff is capped and
   jittered.
7. **Malformed JSON is handled.** Garbage response -> exactly one retry ->
   `vlm: null`. The pipeline exits 0 and the row is written.
8. **Offline completes.** Transport raising a connection error: pipeline
   finishes, exit 0, local features populated, `vlm: null`, and the UI renders a
   named warning.
9. **No key anywhere.** `GEMINI_API_KEY`'s value appears in no log, report,
   fixture or error payload. Reuse `verify_01.sh`'s `scrub()`; also assert no
   absolute path containing the OS username.
10. **The frame carries no metadata.** ffprobe of a sampled frame shows no EXIF,
    no GPS, no timed-metadata stream, no side data beyond the picture.
11. **The frame is tone-mapped.** Against an HDR-tagged fixture (BT.2020
    primaries, HLG transfer), the sampled frame's primaries and transfer are
    BT.709, and its mean luma is inside a sane band rather than the washed-out
    value an untone-mapped extract produces. Print the colour triple and the
    measured mean.
12. **Boundaries survive VFR.** Against the VFR fixture, scene boundaries are
    stored in seconds against the source, and each maps back to a source frame
    within one frame duration. Print max error in ms.
13. **Energy curves are not flat.** Against a fixture with a deliberate motion
    and loudness change, per-scene energy varies by a stated minimum across
    scenes. Print min, max and spread.
14. **Runtime budget.** The guide's per-session budget, measured on synthetic
    clips of equivalent total duration, printed in seconds. `@pytest.mark.slow`
    if it needs to be; report `SKIP` with a reason rather than passing silently.
15. **`scripts/` is linted.** ruff over `scripts/` exits 0 with the engine's
    ruleset, and no `# noqa` was added to get there. Print the rule count.
16. **Ctrl-C is clean.** `make dev` interrupted returns 130 with no traceback on
    stdout or stderr. Print the exit code.
17. **Someone can start it and see the analysis.** Playwright against a real
    `make dev` stack: upload a fixture clip, wait for analysis, and assert the
    per-clip view renders scene tags, an energy sparkline and the disclosure
    text — the same path a person walks, asserting what a person would notice.
    This is the criterion every prompt from here owes.
18. **No regression.** `scripts/verify_02.sh` still exits 0.
19. **`[HUMAN]` — real-footage checklist.** The gate asserts
    `docs/manual-checks/prompt-03.md` exists with **no unticked boxes**; while
    any box is unticked it prints
    `[HUMAN] real phone footage unverified — docs/manual-checks/prompt-03.md`
    and **exits 1**. It never passes on its own, and no agent may tick it.

    Create it as a deliverable, one checkbox per row:

    - [ ] 3+ real gym clips analysed, at least one HEVC/HDR, at least one VFR
    - [ ] Scene boundaries land where the eye says the shot changes
    - [ ] Scene tags describe the actual exercise and environment
    - [ ] The sampled frame looks like the footage — not washed out, not soft
    - [ ] Re-running analysis makes no API calls (daily counter unchanged)
    - [ ] The disclosure is visible at the moment frames are sent
    - [ ] Signed off by: ________  Date: ________

### Definition of done

`make verify-03` green on every criterion (19 including the human line) -> CI
green on the PR -> `docs/reports/prompt-03.md` written -> `/gate 03` -> tag
`prompt-03-done`.

**The report is capped at roughly two pages: decisions and open issues only.**
Prompt 02's ran to 1,237 lines and the ratio had drifted — the report exists so
Ashwin can review a session he did not watch, and a report nobody finishes is
worse oversight than a short one. Keep: decisions made autonomously, assumed
defaults, the Gemini prompt verbatim, deviations, gate status with measured
values, and open issues. Drop the narration.

Then update `docs/chat-context.md`: POSITION, WHAT EXISTS, amendments 007 and
008 in AMENDMENTS IN FORCE, and OPEN ISSUES — with issues 5 and 7 struck if
they were actually fixed, and explicitly "none" if none remain.
