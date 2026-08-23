# Prompt 02 kick-off

Paste the PROMPT block below into Claude Code, in the repo folder, after
`/run-prompt 02`.

**This file deliberately does not restate the guide's deliverables.**
`/run-prompt 02` reads them from `$REPCUT_GUIDE_PATH` itself, and the guide is
gitignored for a reason. What follows is only the part the guide does not
contain: the seven conflicts between Prompt 02 and the repo's binding rules, and
the gate design that resolves them. Same reason `engine/repcut/prompts_data.py`
on the dashboard branch is a problem — a public repo should carry the
amendments, not the plan.

---

## Why this prompt needs an amendment before any code

Prompt 02 is the largest prompt in the guide and the one every later prompt
inherits from. Seven places where it collides with `.claude/rules/`, with the
repo as Prompt 01 left it, or with itself:

| # | Conflict | Resolution |
|---|---|---|
| 1 | Success criterion 1 requires "3 real phone clips (incl. HEVC/VFR)"; `testing.md` forbids committing media | Split: synthetic-fixture gate in `verify-02` + a human-signed manual checklist |
| 2 | `ffmpeg_builder` path: guide says `engine/ffmpeg_builder.py`, `ffmpeg.md` says `engine/media/ffmpeg_builder.py`, the package is `engine/repcut/` | `engine/repcut/media/ffmpeg_builder.py` |
| 3 | "2GB file, RSS < 500MB" cannot run in CI or the fast loop | `@pytest.mark.slow`, disk-gated, file generated at test time |
| 4 | Deliverable 5 says build a design system; `code-style.md` already binds `ui/` to `.claude/skills/repcut-design-system`, and the Autonomy Protocol hands styling to Claude Code | The skill is the source of truth. Prompt 02 *implements* its tokens, it does not invent a parallel set |
| 5 | Scope: DB + Alembic + resumable upload + ffmpeg_builder + WebSocket jobs + full design system + 4 UI surfaces on one branch | Stays one branch, but the engine track must be green and checkpointed before the UI track starts |
| 6 | Deliverable 2 stores bytes under `data/projects/{id}/source/`, but the Constraints require a duplicate to link rather than re-store — across two projects both cannot hold | Content-addressed store under `$DATA_DIR/media/`, derived artifacts included; project folders hold references and output only |
| 7 | "resumable" + "kill engine mid-upload, restart, resume succeeds", but none of the guide's three tables holds in-flight transfer state | `upload_sessions` — durable offset, reconciled against the `.part` file on disk |

Conflict 4 is the one worth being deliberate about. If Claude Code invents
tokens in Prompt 02, every prompt from 03 to 13 inherits them and the skill
becomes a document nobody reads. If it implements the skill, the skill stays the
single place the look is decided.

Conflicts 6 and 7 are the structural ones: they change the schema and the
on-disk layout that every prompt from 03 to 13 reads. They are also the two
that cost the most to retrofit later.

---

## PROMPT — Prompt 02

### Role & Context

```
You are a senior full-stack engineer continuing Repcut from Prompt 01
(merged to main, tagged prompt-01-done, verify-01 green 13/13, `make dev`
works). Build project + media management: chunked local upload, media
library, ingest pipeline, and a preview player — on a design system taken
from .claude/skills/repcut-design-system.

Read PROMPT 02 in full from $REPCUT_GUIDE_PATH, then read
docs/guide-amendments/ 000-003. Amendments 002 and 003 are in force and
change what you may install and what already exists. Work autonomously per
the autonomy protocol in CLAUDE.md. Plan first, and wait for approval on the
plan.
```

### Deliverable 0 — amendment 004, written before any implementation

`docs/guide-amendments/004-prompt-02-fixtures-paths-scope.md`, in the format of
000–003 (What the guide says / What we found / Why the guide's version doesn't
work / Proposed change / Consequences / Principle check). It records the seven
resolutions in the table above, verbatim as decisions, with the reasoning
derived from the rules files they conflict with — not from this prompt.

The amendment is the first commit on `prompt-02`. Nothing else starts until it
exists, because five of the seven change what the code looks like.

### Deliverables 1–7

As stated in the guide's PROMPT 02, amended by 004. Path and dependency
resolutions that override the guide's literal text:

- `engine/repcut/media/ffmpeg_builder.py` — the single place FFmpeg and ffprobe
  argv lists are constructed. `list[str]` only, never `shell=True`, never
  string concatenation. Unit-tested as command strings against snapshots before
  any file is touched.
- `engine/repcut/db/` — SQLAlchemy 2 async models, `alembic/` at the engine
  root, `aiosqlite` driver. Every migration runs forward and backward on an
  empty DB in the gate.
- `ui/lib/design/tokens.ts` (or Tailwind theme extension — your call, record it)
  is generated from `.claude/skills/repcut-design-system`'s token scale. Where
  the skill gives a range rather than a value (the accent hue is the only one),
  choose, and record the choice and the measured contrast ratio in the report.
- New dependencies, all free and AGPL-compatible — verify each licence and
  record it in the report per `frontend-and-licensing.md`:
  `sqlalchemy[asyncio]>=2`, `aiosqlite`, `alembic`, `python-multipart`;
  dev: `psutil` (RSS measurement — `resource` is POSIX-only and this is a
  Windows machine).

**Do not install torch, torchvision or torchaudio** (amendment 003). Nothing in
this prompt needs them.

### Constraints

Everything in `.claude/rules/` applies. The ones this prompt will actually hit:

- **Design system, conflict 4.** `.claude/skills/repcut-design-system` is the
  source of truth for tokens, editor shell layout, job states and the
  AI-suggested control. Prompt 02 implements it. If the skill is wrong or
  underspecified, amend the skill in the same commit — do not diverge from it
  silently. No ad-hoc hex in components; Tailwind core utilities only; no
  component libraries.
- **Legal line.** CapCut/Premiere *patterns* only. Zero cloned assets, icons,
  exact colour values, fonts or copy text. No product screenshots stored in the
  repo. If it looks like a Tailwind default template it fails this prompt.
- **Streaming, not buffering.** 8MB chunks. Never load a whole file into
  memory. Uploads are local-machine moves.
- **VFR, rotation, audio rate.** Per `ffmpeg.md`: normalize to CFR in the proxy,
  store *both* source and normalized fps in `media_files`, read and apply
  rotation side-data rather than trusting raw dimensions, resample audio to one
  project rate. This is the classic silent-desync bug and the gate must prove it
  is handled, not assert that it was considered.
- **Proxies are preview-only.** Every later prompt processes originals.
- **Named exceptions.** `FFmpegEncodeError`, `FFmpegFilterGraphError`,
  `UnsupportedCodecError`, plus `CalledProcessError` from ffprobe with the
  stderr tail in the job's error field. Never a bare `except Exception:`, never
  a raw traceback surfaced to the UI, never a silently stuck job.
- **Duplicate hash links, does not re-store.**
- **make is GNU Make 3.81** — no `.ONESHELL`. Every recipe line runs in its own
  shell. Keep multi-step recipes on one line chained with `&&`, matching the
  existing Makefile's documented pattern.
- **Secrets, absolute.** No media committed, ever — not a 2-second `.mp4`.
  `data/` stays gitignored. No absolute path containing the OS username in any
  log, report, test fixture or error message; reuse `verify_01.sh`'s `scrub()`.

### Sequencing (conflict 5)

One branch, `prompt-02`, but two tracks with a hard checkpoint between them:

1. **Track A — engine.** Amendment 004 → schema + migrations →
   `ffmpeg_builder` + its snapshot tests → chunked resumable upload → ingest
   job (ffprobe, thumbnail strip, proxy) → `/ws/jobs`. Criteria 1–8 of the gate
   green. Run `/checkpoint` here. If the session ends, this is a coherent place
   to stop.
2. **Track B — UI.** Design tokens from the skill → shared components → editor
   shell → dashboard, drag-drop upload, library grid, proxy player. Criteria
   9–13.

Delegate Track A to `engine-architect` and `video-pipeline-engineer`, Track B to
`frontend-engineer`. Author the gate with `gate-runner` as you go, not at the
end.

### Autonomy Protocol

Fully autonomous. Decide implementation details, defaults (record under
"Assumed" in the report), and bug fixes anywhere you find them. Styling
decisions are constrained by the design-system skill, not free — that is the
one change amendment 004 makes to the guide's protocol.

Stop and ask only for: P1–P5 conflicts, anything paid, contradictory
requirements, destructive actions outside the repo, anything touching a
credential. Prompt 02 is **not** a human-review checkpoint.

### Success Criteria (= `make verify-02`)

`scripts/verify_02.sh`, same contract as `verify_00.sh` / `verify_01.sh`:
binary, exit-coded, per-criterion, idempotent, prints the **measured value**
next to each verdict. See `.claude/skills/verify-gate-authoring`.

All fixtures are generated at test time by a `conftest.py` factory
(`ffmpeg -f lavfi`). No media is committed.

1. **Migrations round-trip.** `alembic upgrade head` then `downgrade base` then
   `upgrade head` on a scratch DB, exit 0 each time. All six tables of
   amendment 004 present — `projects`, `media_blobs`, `media_files`,
   `derived_artifacts`, `upload_sessions`, `jobs` — with `fps_source` and
   `fps_normalized` on `media_blobs`, and the unique constraint on
   `(sha256, artifact_kind, params_version)`.
2. **ffmpeg_builder snapshots.** `pytest engine/tests/test_ffmpeg_builder.py`
   green. Every generated argv matches its snapshot; asserts `shell=True`
   appears nowhere in `engine/`, and that no builder emits a path containing
   `Users/`.
3. **Non-video rejected.** Upload of a `.txt` and of an `.mp4`-named non-video
   both return a named error, not a 500. No row written.
4. **Resumability.** Engine started as a subprocess, upload begun, process
   `SIGKILL`ed mid-transfer, engine restarted, upload resumed → file completes,
   hash verifies, exactly one `media_files` row. The resumed offset comes from
   `upload_sessions` reconciled against the `.part` file, not from the client.
   Re-running the whole criterion leaves the DB in the same state.
5. **Duplicate hash.** Same file uploaded twice, into *two different projects* →
   one stored blob on disk, two `media_files` references, and one set of derived
   artifacts: byte count under `$DATA_DIR/media/` unchanged after the second
   upload, and the second ingest re-encodes no proxy. Print the measured byte
   count and the derived-artifact row count.
6. **VFR normalization.** Against a deliberately VFR fixture: ffprobe of the
   *source* shows non-uniform frame durations, ffprobe of the *proxy* shows CFR,
   and cumulative A/V drift at end of clip < 40ms. Print the measured drift in
   ms. This criterion is the reason the fixture factory exists.
7. **Rotation metadata.** Fixture with a rotate side-data tag → stored
   resolution is the *display* resolution, not the raw pixel dimensions. Print
   both.
8. **Ingest artifacts.** Thumbnail strip has `ceil(duration/2)` frames; proxy is
   720p H.264, playable, duration within ±0.1s of source, audio stream present
   at the project sample rate.
9. **Job lifecycle over WebSocket.** Connect to `/ws/jobs`, run an ingest,
   assert the observed sequence contains `queued` → `running` (with monotonic
   percent and a non-empty step name) → `succeeded`. A forced-failure job
   yields `failed` with a human-readable cause and no traceback in the payload.
10. **UI clean and builds.** `tsc --noEmit`, `npm run lint`, `npm run test`,
    `npm run build` all exit 0. Zero `any` in `ui/**/*.{ts,tsx}`.
11. **Tokens are the only source of style.** No hex literal and no
    `rgb(`/`hsl(` in `ui/app/**` or `ui/components/**` outside the single tokens
    file. Print the offending files. This is what stops later prompts from
    drifting.
12. **Accessibility baseline.** Every custom control has a role and an
    accessible name; the player is keyboard-operable (space, ←, →);
    `prefers-reduced-motion` is respected somewhere in the motion layer; text
    contrast ≥ 4.5:1 against its token background, computed, not asserted.
13. **Large-file memory.** `@pytest.mark.slow`, skipped when free disk < 5GB or
    `REPCUT_SLOW=0`. Generates a 2GB file at test time, uploads it, samples
    engine RSS via psutil throughout, asserts peak < 500MB, deletes the file.
    Print peak RSS in MB. Excluded from CI and from `make test`; the gate runs
    it and reports SKIPPED-with-reason rather than passing silently.
14. **No regression.** `scripts/verify_01.sh` still exits 0.
15. **Nothing forbidden tracked.** `git ls-files` contains no `.mp4`, `.mov`,
    `.hevc`, `.wav`, `.mp3`, nothing under `data/`, and no file matching the
    guide's filename patterns.
16. **`[HUMAN]` — real-footage checklist.** The guide's "3 real phone clips"
    criterion cannot be automated without committing footage. The gate asserts
    `docs/manual-checks/prompt-02.md` exists and contains **no unticked
    boxes**; while any box is unticked it prints
    `[HUMAN] real phone footage unverified — docs/manual-checks/prompt-02.md`
    and **exits 1**. It never passes on its own.

    Create `docs/manual-checks/prompt-02.md` as a deliverable, gitignore
    nothing about it (it holds no media, only verdicts), and give it one
    checkbox per row:

    - [ ] 3+ real phone clips uploaded, at least one HEVC, at least one VFR
    - [ ] Library metadata matches what the phone/ffprobe reports for each
    - [ ] Thumbnails correct and correctly oriented (portrait stays portrait)
    - [ ] Proxies scrub smoothly; no audio desync at end of the longest clip
    - [ ] Duplicate upload of the same clip links rather than re-stores
    - [ ] Signed off by: ________  Date: ________

### Definition of done

`make verify-02` green on every criterion (16 including the human line) → CI
green on the PR → `docs/reports/prompt-02.md` written, recording: assumed
defaults, the accent hue chosen and its contrast ratio, licences verified for
the four new dependencies, and anything the gate passed *around* rather than
through — including, under **OPEN ISSUES**, that refcounting and orphan
collection for the content-addressed store are deferred to Prompt 12 (or to the
first earlier prompt that ships a delete surface), per amendment 004
→ `/gate 02` → tag `prompt-02-done`.

Then update `docs/chat-context.md`: POSITION, WHAT EXISTS, amendment 004 in
AMENDMENTS IN FORCE, and OPEN ISSUES (explicitly "none" if none).
