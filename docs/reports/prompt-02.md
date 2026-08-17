# Prompt 02 — Media pipeline & design system
Branch: prompt-02 · Date: 2026-08-10

**Status: Track A and Track B both built. Every automated criterion passes; 16
is the human one and is still open.** The two-track split is
[amendment 004](../guide-amendments/004-prompt-02-fixtures-paths-scope.md) §5:
the engine half had to be green and checkpointed before the UI half began, and
it was. Criteria 1–9 are the engine, 10–13 the UI, 14–15 regression and hygiene;
16 needs a person with a phone and can never pass on its own.

Criteria 10–13 were hard-coded failures reading "(Track B not built yet)" until
this session. They are executable checks now — including criterion 13, which
**measured peak engine RSS at 331 / 346 / 347MB across three runs, against a
500MB budget, while receiving 2GB**.

`make verify-02`: **every automated criterion passes; criterion 16 fails and
should.** It needs a person, a phone and six ticked boxes in
`docs/manual-checks/prompt-02.md`.

**Criterion 16 has been attempted twice, and each attempt found a blocker none
of the automated criteria could see.**

The first: on Windows, `make dev` booted the engine onto an event loop with no
subprocess transport, so every FFmpeg call was dead and every upload 500'd at
finalize. Fixed, along with two independent faults in the same trace — a
traceback leaving the engine in an HTTP response body, and a host failure being
reported to the user as unreadable footage. Criteria 17 and 18 date from there.

The second: `make dev` itself could not be relied on to produce a working stack.
It orphaned `next dev` on Ctrl-C, printed both service URLs for a UI that had
already died, and the jobs socket never connected in any run — refused by the
page's own Content-Security-Policy, before a handshake was ever sent. Five
defects, all fixed, plus two more found while fixing them. **Criteria 19 and 20
are new and are the structural answer**: 19 drives the launcher's lifecycle, 20
starts `make dev` and opens the app in a real browser. The full account is under
*Decisions made autonomously*; the general lesson — four green signals over a
broken product, which is one flaw and not four — is under *Open issues*.

A **full security review** ([security-review-2026-08-07](security-review-2026-08-07.md))
also landed against this branch — ten findings, three of them High, all fixed
with regression tests. Its own follow-up was that the engine suite could not be
run in the review sandbox; it has now been run here and is green.

This report is written as the work lands rather than at the end, so what is
below is what is true today.

## Built

### Earlier in this prompt

- **`docs/guide-amendments/004-…`** — seven collisions between Prompt 02 and the
  binding rules, resolved before any code was written.
- **`engine/repcut/config.py`** — `detect_sync_root()` /
  `warn_if_data_dir_synced()`. Returns a provider label only; the path carries
  the OS username and is never logged.
- **`engine/repcut/db/`** — the six tables of amendment 004 as SQLAlchemy 2
  async models (`models.py`), the constraint naming convention (`base.py`), the
  async engine and session factory (`session.py`), and `types.py`.
- **`engine/alembic/versions/0001_initial_schema.py`** — the migration those
  models round-trip against.
- **`engine/repcut/media/artifacts.py`** — `ArtifactKind`, the recipe
  parameters, and the `PARAMS_VERSION` table that keys derived artifacts.
- **`engine/repcut/media/ffmpeg_builder.py`** — every FFmpeg and ffprobe
  invocation: the probe, the 720p CFR proxy, the tiled thumbnail strip, the
  two-second dry run, typed errors from classified stderr, and an async runner
  that renders to a temp name and moves it into place.
- **`engine/tests/conftest.py`** — a `make_clip` factory generating synthetic
  clips at test time, including genuinely variable-frame-rate ones.

### This session

- **`engine/repcut/media/store.py`** — the on-disk layout of amendment 004 §6 as
  functions. Every stored path is `$DATA_DIR`-relative and POSIX-separated, and
  no component derives from user input — the blob's extension comes from the
  container ffprobe reported, not from what the upload was called.
- **`engine/repcut/media/metadata.py`** — one ffprobe document into the
  properties `media_blobs` stores. Owns rotation, the source audio rate, and the
  three-valued VFR answer.
- **`engine/repcut/api/`** — `errors.py` (named errors, one renderer),
  `schemas.py`, `deps.py`, `projects.py` (projects, library, reingest),
  `uploads.py` (chunked resumable transfer), `jobs.py` (`/jobs`, `/ws/jobs`).
- **`engine/repcut/jobs.py`** — the in-process job worker, its event stream and
  the monotonic progress reporter.
- **`engine/repcut/media/ingest.py`** — probe → thumbnail strip → proxy, keyed
  and skipped by `(sha256, kind, params_version)`.
- **`engine/repcut/db/migrations.py`** — the engine migrates itself at startup.
- **`scripts/verify_02.sh`** + **`scripts/verify_02_checks.py`** — the gate, and
  the measurements behind it.
- **`docs/manual-checks/prompt-02.md`** — criterion 16's checklist.
- **`engine/repcut/security.py`** + **`.claude/rules/security.md`** — the engine's
  network boundary and the threat model behind it, from the security review.

### After the first real-footage run

- **`engine/repcut/loop.py`** + **`engine/repcut/__main__.py`** — the event loop
  the engine requires, and `python -m repcut`, the single entry point every
  launcher now goes through so none of them can choose a different one.
- **`engine/repcut/redaction.py`** — `redact_paths`, moved out of
  `ffmpeg_builder` so `api/errors.py` can reach it without the API layer
  importing the media layer.
- **`UnexpectedErrorBoundary`** in `api/errors.py` — the outermost ASGI layer,
  turning any unnamed failure into named JSON and keeping the traceback in the
  log, scrubbed.
- **`FFmpegUnavailableError` / `FFmpegLoopError` / `MediaToolingUnavailableError`**
  — the type split that separates "the engine is broken" from "this file is not
  a video", and the 503 that leaves a finished transfer resumable.
- **`engine/tests/test_loop.py`**, **`engine/tests/test_error_boundary.py`**, and
  gate criteria **17** and **18**.

### Track B — the UI

- **`ui/app/globals.css`** + **`ui/tailwind.config.ts`** — the design system's
  tokens, and Tailwind bound to them rather than to its own scale. The accent is
  `#b49bff`, the one hue that means "the AI decided this"; its measured contrast
  is 8.41 / 7.76 / 7.06 against surface / panel / raised, and the whole table is
  in the skill, amended in the same commit as amendment 004 §4 requires.
- **`ui/app/fonts/`** — Sora and IBM Plex Sans, latin-subset woff2, both SIL OFL
  1.1, loaded through `next/font/local`. Provenance in `fonts/README.md`.
- **`ui/components/primitives/`** — Button, Badge, Panel, Progress, Skeleton,
  Slider, Modal, AiSuggested. No `className` escape hatch on any of them.
- **`engine/repcut/api/media.py`** — `GET /media/{id}/proxy` and
  `/thumbnail-strip`, with Range support, because a player that cannot seek
  refetches from byte 0 on every frame step.
- **`ui/lib/api/`** — `engine.ts` (the browser's origin, and why it is public),
  `schemas.ts` (Zod mirrors of the engine's models), `client.ts` (every call a
  discriminated result, never a throw), `server.ts` (`server-only`, first-paint
  reads).
- **`ui/lib/upload.ts`** — the chunked uploader: `File.slice()` throughout,
  hash-wasm for an incremental digest, resume-by-hash, bounded offset resync.
- **`ui/lib/jobs/useJobStream.ts`** — `/ws/jobs` with reconnect and backoff.
- **`ui/components/`** — `Dropzone`, `UploadQueue`, `MediaCard`, `ProxyPlayer`,
  `JobList`, `NewProject`, `EngineDown`, and `Workspace`, the editor shell.
- **`ui/app/page.tsx`**, **`ui/app/projects/[id]/page.tsx`** — the dashboard and
  the editor, both `force-dynamic`.
- **`engine/repcut/jobs.py`** + **`api/jobs.py`** — `JobQueue.cancel` and
  `POST /jobs/{id}/cancel`, the missing half of the job contract.
- **`engine/tests/test_large_upload.py`** — criterion 13.
- 276 engine tests and 153 UI tests, all CPU.

## Decisions made autonomously

### The sync-root guard existed and could not fire

**This is the finding worth Ashwin's attention**, because the shape of the bug
matters more than the bug.

`warn_if_data_dir_synced()` was added in `1a666fb` with tests, and DATA_DIR was
sitting inside OneDrive, and no warning ever appeared. Both halves were
individually correct. Measured, on the real path rather than a fixture:
`detect_sync_root()` returns `onedrive` for the repo-anchored default, via the
`ONEDRIVE` environment variable *and* via the folder-name fallback
independently. Detection was never the problem.

The problem was that the function had exactly one caller — the FastAPI
`lifespan` — and **nothing in the project opens a lifespan scope**. httpx
0.28.1's `ASGITransport`, which every engine test uses, implements the HTTP
scope only; the string `lifespan` does not appear in its source. So the guard
was unreachable from the test suite, from `make test`, from `verify_01.sh`'s
`/health` criteria and from `alembic upgrade`. It ran only under a real uvicorn
boot, i.e. only when someone ran `make dev`.

That is worse than having no guard. A gate that never executes a check still
prints PASS, and the PASS reads as evidence the path was checked.

**Fixed by moving the call to `get_settings()`** — the one function every entry
point calls before it can touch DATA_DIR, and `lru_cache`d, so it stays one
warning per process. Constructing `Settings` directly stays silent, which is
what fixtures want. `lifespan` now calls `configure_logging()` twice on
purpose: resolving settings emits the warning, but the level it renders at is
itself a setting, so it configures defaults, resolves, then reconfigures.

Two tests, both verified to fail against the old code before being kept:

| Test | Pins |
|---|---|
| `test_resolving_settings_fires_the_guard` | The guard is wired to settings resolution, not to an ASGI server. Fails with `Right contains one more item: 'data_dir_under_cloud_sync'` against the pre-fix `get_settings`. |
| `test_the_default_relative_data_dir_is_caught_inside_a_synced_repo` | The exact condition that occurred: `DATA_DIR=./data` anchors on the repo root, so a repo cloned into a synced folder puts the media store under it with nobody choosing that. |

`DATA_DIR` has since been repointed outside the synced tree, and
`check_env.py`'s row now reads OK.

### `DateTime(timezone=True)` is a lie on SQLite — `UTCDateTime` (`db/types.py`)

Measured directly, aiosqlite + `DateTime(timezone=True)`: write
`datetime.now(UTC)`, read back `tzinfo=None`. The driver serialises to a string
with no offset and reconstructs a naive value. Nothing fails at the write and
nothing fails at the read — it fails at the first `stored < utcnow()`, with
`TypeError: can't compare offset-naive and offset-aware datetimes`, in whatever
module needs that comparison first. **Prompt 03's Gemini cache expiry is a
stored-vs-now comparison, so Prompt 03 would have inherited a bug written
here**, at a call site with no visible connection to the column definition.

`UTCDateTime` is a `TypeDecorator` over `DateTime(timezone=True)` and is now the
type of every datetime column in the schema. It re-attaches UTC on read, and on
write it **rejects a naive value rather than coercing it**. Coercion would
assume the caller meant UTC; a caller that passed `datetime.now()` on this
machine (CET) did not, and would store a value an hour off that then compares
cleanly against everything and is simply wrong. A loud error at the boundary
beats a quiet one two prompts downstream.

`0001_initial_schema` still reads `sa.DateTime(timezone=True)` and is not
wrong: `UTCDateTime` adds nothing to the DDL, only Python-side behaviour, so the
migration keeps stating the storage type and stays free of application imports.
The migration docstring says so, so the next reader does not go looking for a
divergence. The drift test (alembic `compare_metadata`) confirms the two sides
agree.

Round-trip covered by `test_a_persisted_timestamp_comes_back_timezone_aware`,
which expunges the identity map so the assertion is on a real load rather than
on the object Python still holds — `expire_on_commit=False` would otherwise
have made it a tautology.

### `upload_sessions` had durable state but no way to find it

Amendment 004 §7 gave the table a durable offset, which is what makes success
criterion 4 idempotent. It did not give it a **lookup path**: every read was by
session id, so resume worked exactly for a caller that still held the id.

Criterion 4 kills the engine and restarts it, and the test client holds the id
in memory across the kill — so the criterion passes while the real case does
not. A browser tab refreshed mid-upload has lost the id, retries as a new
session, and leaves the first `.part` on disk with nothing referencing it. The
gate would have certified resume as working on the strength of a client that
cannot forget.

Added a **partial unique index**, `uq_upload_sessions_in_progress` on
`(project_id, declared_sha256) WHERE status = 'in_progress'`, in both the models
and `0001_initial_schema`. It is simultaneously the lookup path and the
constraint: a second in-flight transfer of the same clip into the same project
is now impossible rather than merely unlikely, so the orphaned `.part` cannot be
created in the first place.

Partial on two counts, both deliberate:

- **Only `in_progress`.** Re-uploading a clip whose earlier transfer completed
  or was aborted is legitimate; a full unique index would forbid it.
- **NULLs stay distinct.** `declared_sha256` is nullable, and SQLite treats
  NULLs as distinct in a unique index, so sessions whose client declared no hash
  do not participate. Nothing identifies them, so nothing may claim they
  collide.

Five tests cover the four cases plus the cross-project one. Asserted against the
migration as well as the models (`test_the_resume_lookup_index_is_partial`),
because the models are what tests build from and the migration is what a real
database is built from.

**Track B inherits an obligation from this.** The UI must **look up in-progress
sessions for the project on mount** — `GET` the in-progress session for
`(project_id, declared_sha256)` and resume it — rather than assuming it holds
the session id it started with. A page refresh, a crashed tab and a reopened
browser all arrive with no id, and the index means the retry will now fail with
an integrity error instead of silently orphaning a part-file. That failure is
the signal to resume, and the UI has to act on it.

The engine side of that lookup does not exist yet; it belongs with the upload
endpoint, which is not written.

### `params_version` is now enforced, not remembered

The obligation recorded in the previous session as *"owed by the next commit"* is
discharged. `test_every_recipe_argv_matches_its_params_version` freezes each
recipe's argv keyed by `(kind, params_version)` and fails in both directions.
Both were run, not assumed:

| Change made | What the suite said |
|---|---|
| `crf` 23 → 22, version left at 1 | *"the proxy recipe changed but PARAMS_VERSION[proxy] is still 1. Bump it in engine/repcut/media/artifacts.py in this same commit and freeze the new argv here…"* |
| version 1 → 2, recipe unchanged | *"PARAMS_VERSION[proxy] is 2 and RECIPE_ARGV has no argv frozen at that version…"* |

The recipe *parameters* moved into `artifacts.py` beside the versions, so a
change and its bump are one edit in one file. They are deliberately **not** in
`Settings`: an environment variable that changes the bytes an artifact is made
of, without changing the key those bytes are stored under, is exactly the
staleness `params_version` exists to prevent. `ffmpeg.md`'s "presets live in
config, not in code" is honoured as *presets are declared data in one place*,
not as *presets are user-editable at runtime*.

### A bug found in Prompt 00's tooling, fixed here

`.pre-commit-config.yaml` pinned `ruff-pre-commit` at **v0.8.4** while
`engine/pyproject.toml` declares `ruff` unpinned, so `make lint`, CI and the
venv all resolve **0.16.1**. The two format differently — 0.9 changed how an
assert's message is wrapped — so the commit hook *rewrote* a file into a shape
`ruff format --check` then rejects.

This is live, not theoretical: it surfaced by failing a commit in this session,
and any PR carrying such a file would have failed CI on a diff the author never
wrote. Pinned the hook to v0.16.1 with a comment saying to keep the two in step.
No formatting rule was weakened.

### Two things the builder measured that the docs had wrong

**`r_frame_rate != avg_frame_rate` is not a reliable VFR test — it depends on
the container.** The `ffmpeg-recipes` skill states it flatly. Measured on one
clip of 52 frames with deliberately uneven timestamps, muxed both ways:

| Container | `r_frame_rate` | `avg_frame_rate` | Heuristic detects VFR |
|---|---|---|---|
| MP4 | `30/1` | `1560/121` (≈12.9) | yes |
| Matroska | `30/1` | `30/1` | **no** |

Same frames, same timestamps, opposite answers. It is adequate for Prompt 02 —
phones record MP4/MOV — but it is a container-dependent heuristic, not a
property of the file, and the ingest job must not treat a negative as proof of
CFR. The VFR fixture is written to MP4 for that reason, and the integration test
asserts the fixture is VFR *before* asserting the proxy is not, so it cannot
quietly become a test of nothing.

**A temp file named `.proxy.mp4.partial` cannot be written by FFmpeg.** The
muxer is chosen from the output extension, and `.partial` is not one, so the
first render attempt failed with `Error opening output files: Invalid argument`
— which reads like a permissions problem and is not one. The temp name keeps the
real suffix last: `.proxy.partial.mp4`.

The stderr classifier's patterns were also taken from ffmpeg 8.1's actual output
rather than from memory: the wording moved between major versions
(`filtergraph` → `filterchain`), and the first draft silently degraded every
filter-graph bug to the generic case. The verbatim strings are in the test file.

### The security review found the layer nothing had been written to cover

Full detail in [security-review-2026-08-07](security-review-2026-08-07.md); the
part worth carrying forward is *where* the findings were. Enabling `ruff`'s `S`
ruleset across the engine produced **zero** pre-existing findings, and the review
found no hardcoded secrets, no SQL injection, no command injection, no unsafe
deserialisation and no XSS sinks. The code was disciplined. The gaps were
architectural — three High findings, all in the boundary layer that had simply
never been written:

- **The engine had no `Host` or `Origin` check at all.** Prompt 02 is the prompt
  that added the first mutating routes (`POST /projects`, `POST /uploads`,
  `PUT /chunk`, `POST /finalize`), so it is the prompt where "bound to loopback"
  stopped being sufficient. A browser tab is a program an attacker controls,
  running on the trusted machine, and CORS governs only whether a reply is
  *readable*.
- **`/ws/jobs` was readable cross-origin.** `CORSMiddleware` never sees a
  WebSocket scope, so any page on the internet could open the stream and read
  job ids, project ids, content hashes and clip failure causes — a log of what
  the user films and when. This is a P4 leak, not just a security bug.
- **`store.py`'s docstring promised what nothing enforced.** It said "no path
  component derives from anything the user typed"; `blob_directory` and friends
  interpolated their arguments straight in, and `PurePosixPath` normalises
  nothing. A prose invariant with no assertion behind it is the same failure
  shape as the sync guard above — it reads as covered.

The `next@14 → 16` upgrade was the one item that needed a human: it is a major
framework bump, and 14.2.35 is the end of its line with six high-severity
advisories and no patch coming. **Ashwin approved it before it was made.** React
stayed on 18 to keep the blast radius to the framework itself.

### The job socket was unreadable by the UI, and said nothing about it

`/ws/jobs` sends `JobEvent` — keyed `job_id`, and carrying no `created_at`,
because an event is one observation of a job rather than the row. The hook
parsed frames with `jobSchema`, which is the shape of `GET /jobs`. **Every frame
failed the parse.**

What makes this worth writing down is why nothing complained. A frame that fails
to parse is *how the keepalive is recognised* — the engine pings through the
quiet so a half-open socket is noticed, and the client cannot treat that ping as
a job. So the correct handling of one case silently swallowed the other, and the
symptom was a jobs panel that stayed empty with no error anywhere, on either
side. It is the same shape as the sync-guard finding above: a check that reads
as covered because the failure path is indistinguishable from a normal one.

Fixed with a `jobEventSchema` that mirrors the socket payload, and the contract
is now pinned from **both** ends —
`test_the_socket_payload_carries_the_fields_the_ui_parses` asserts the field set
from the engine, `schemas.test.ts` asserts the same list from the UI and that
the two shapes are *not* interchangeable. A rename fails a suite instead of
blanking a panel.

### No job could be cancelled, and fixing that found two deeper faults

`.claude/rules/frontend-and-licensing.md` requires cancel on every long job.
Nothing had it: `JobStatus.CANCELLED` existed in the enum with no route and no
caller. The longest thing here is an FFmpeg encode, so without cancel a render
that hits the timeout leaves restarting the engine as the only way out.

`POST /jobs/{id}/cancel` closes that. Building it exposed two faults that were
worse than the missing feature:

**Cancel had a window where it did nothing but reported success.**
`_mark_running` commits `running` before `_execute` creates the handler task. A
cancel arriving in between found no task to interrupt and no queued row to
claim, so it returned `False`, the route answered 200, and the job ran to
completion. Not theorised — **it is what the 2GB test hit**: its ingest job went
on probing after the cancel. The request is now recorded and applied the moment
the task exists, and `test_a_cancel_between_running_and_the_task_still_stops_the_job`
holds `_mark_running` open to aim at the window. It was run against the unfixed
code first and fails there with `assert True is False`.

**Cancelling a task never stopped FFmpeg.** `asyncio.wait_for` raises
`CancelledError` into the awaiting task and leaves the child process alone. So a
cancelled encode ran to completion, orphaned, still holding a core and the
output handle — and the same applied to the engine's own shutdown, which is the
leak `JobQueue._work`'s comment claims to prevent. The kill is explicit now,
through a helper that tolerates a child which exited between the decision and
the call, and `JobQueue._work` awaits the cancelled job rather than closing the
loop underneath its database write. Verified by removing the fix and watching
`test_a_cancelled_render_kills_the_ffmpeg_process` fail.

### Criterion 13 measured 331–347MB, and the test had to earn the right to say so

2GB of real video through the chunked endpoint, engine RSS sampled every 50ms:
**peak 331MB and 347MB on two runs, from a ~90MB baseline, against a 500MB
budget.** Three and a half minutes, 5GB of disk, so it is `slow` and only the
gate runs it.

The engine here *is* the test process — the suite drives the ASGI app
in-process — so the test has to hold to the discipline it measures.
`conftest`'s `upload_clip` reads the whole file with `read_bytes`, which is
right for a 100KB fixture and would have failed this on the test's own
allocation while proving nothing about the engine. This one holds one 8MB slice
at a time, which is also the browser's chunk size, so what is measured is the
transfer shape the UI actually produces.

The fixture is raw frames rather than an encode: 2GB of x264 would cost half an
hour, 2GB of `rawvideo` costs a disk write, and both are equally real to the
upload path.

### The player is keyed, not reset

Selecting another clip must not inherit the previous one's position. Doing that
in an effect renders one frame of the old timecode against the new clip and then
spends a second render correcting it — and ESLint's `set-state-in-effect` rule
is right to call it out. The player is split so the stateful half is keyed by
clip id: a new selection mounts a fresh component, and the reset is structural
rather than corrective.

### The UI's half of the resume obligation

The engine side landed last session; this is the client. A transfer looks up
`(project_id, sha256)` **before** opening one, so a refreshed tab, a crashed tab
and a reopened browser all find the session they no longer hold the id for. Only
a genuine `upload_not_found` means "start a new one" — anything else is a real
failure, and opening a fresh session on top of it would recreate the orphaned
`.part` the partial unique index exists to prevent.

The other half is the offset. A refused chunk is **re-asked, never guessed**:
`chunk_offset_mismatch` triggers a bounded resync against the engine's number.
Accepting a chunk at an assumed offset writes a hole that surfaces only as a
hash mismatch at the end of a multi-gigabyte transfer — an hour of someone's
evening to learn the upload was wrong from its second chunk. Both paths are
tested against a fake engine written from `uploads.py` rather than from the
client, because a fake shaped around the client would agree with whatever the
client did.

### `make dev` ran the engine on a loop that could not start FFmpeg

Found by **criterion 16** — the human check, on real footage — after twenty
automated criteria had passed. Every upload finalized with a 500:
`NotImplementedError`, raised from
`asyncio/base_events.py::_make_subprocess_transport`.

**Diagnosed, not guessed.** `SelectorEventLoop` inherits that method, whose body
is `raise NotImplementedError`, so on it every `create_subprocess_exec` in
`ffmpeg_builder` is dead. Python has defaulted to Proactor on Windows since 3.8,
so nothing in `engine/` had to install a policy for this to happen — uvicorn
selects the loop itself, and `Config.use_subprocess` is true whenever `--reload`
or `workers > 1` is set. `make dev` was the only launcher passing `--reload`.
The obvious fix — installing `WindowsProactorEventLoopPolicy` before uvicorn
starts — was tried and **measured to do nothing**: uvicorn 0.36+ calls
`asyncio.Runner(loop_factory=…)`, and a Runner given a factory never consults
the policy. `--loop asyncio` maps to the same branching factory. Both are
recorded in `repcut/loop.py` because both look like they should work.

**Chose (a), the loop — not (b), the worker thread.** `create_subprocess_exec`
is not blocking work on the loop; it is already fully async, so
`code-style.md`'s "blocking work goes to a thread/process executor" does not ask
for it. Moving it would rewrite the stdout progress drain and the kill-on-cancel
path, and it would not stop there: `ProgressReporter.fraction` is called
synchronously from inside that drain and reaches `JobQueue.publish`, which does
`put_nowait` on `asyncio.Queue` — not thread-safe. Option (b) trades one branch
in a dependency for a data race in criterion 9's fan-out.

The fix is an entry point rather than a flag: `python -m repcut`
(`repcut/__main__.py`) passes `repcut.loop:event_loop` as uvicorn's custom
`--loop`, and `dev.sh`, `verify_01.sh` and `verify_02_checks.py` all go through
it, so the three launchers can no longer disagree. It is **not** Windows-only
code that assumes Windows: `event_loop()` forces Proactor on `win32` and returns
the platform's own default everywhere else, and `can_spawn_subprocesses()` reads
the capability off the loop object — comparing the inherited method by identity
— rather than matching a class name, so uvloop or any future loop is judged on
what it implements. If the engine is booted some other way anyway, three
surfaces say so instead of a 500: an ERROR at startup, `/health`'s new
`event_loop` / `event_loop_can_spawn` fields (rendered on `/status`), and a named
`FFmpegLoopError` on the request.

**The second bug was independent of the loop.** `run()` caught only
`FileNotFoundError` and `PermissionError`, so anything else escaped to Starlette,
which answers 21 bytes of `text/plain` the UI's Zod parse cannot read — and
logged nine traceback frames of absolute paths, every one carrying the OS
username (`secrets.md`). `UnexpectedErrorBoundary` is now the outermost ASGI
layer: unnamed failures become `internal_error` JSON, the traceback stays in the
log with `redact_paths` applied, cancellation still propagates, and a response
already on the wire is not rewritten. `redact_paths` moved to `repcut/redaction.py`
— it lived in `ffmpeg_builder`, which is exactly why only FFmpeg's output was
ever scrubbed. Pinned by `test_error_boundary.py` and by gate criterion 18,
which reads raw finalize bodies off the wire and searches them for `Users`,
`Traceback` and `File "`.

**A third fault the same trace exposed, and the expensive one.**
`_probe_or_reject` caught `FFmpegError` flat and answered "this file is not a
video" — then aborted the session. An engine that cannot *start* ffprobe would
therefore have told the user their footage was broken and closed a completed
multi-gigabyte transfer. `FFmpegUnavailableError` now splits host faults from
verdicts on the bytes: 503 `media_tooling_unavailable`, no abort, session left
`IN_PROGRESS` and resumable. A file ffprobe genuinely read and rejected still
closes its session — tested in both directions, so the fix did not swing too far.

**The gate gap is the finding, and it was narrower than it looked.** In-process
ASGI was never the problem: criterion 4 already booted a real uvicorn subprocess
and drove real HTTP. The gap was one argument. The gate booted without
`--reload` and `make dev` boots with it, and on Windows that single flag changes
which loop uvicorn builds — so twenty criteria proved the pipeline worked in a
configuration nobody ran. Criterion 17 boots `Engine(reload=True)`, asserts
`dev.sh` launches the shared entry point, asserts `/health` reports a loop that
can spawn, and then drives upload → finalize → ingest through it.

**The `.part` files resume, and there is no third bug.** Measured against the
live database rather than reasoned about: three sessions, all `in_progress`, each
with `bytes_received` equal to both the bytes on disk and the declared size —
2,287,404,000 bytes in total, and `media_files` and `media_blobs` both empty. The
old code never reached `_abort` on this path (the exception escaped above it), so
nothing was closed. Re-dropping the same three files re-hashes them in the
browser, finds the open sessions by `(project_id, sha256)`, computes a resume
offset equal to the file size, sends **zero** chunks and goes straight to
finalize. Nothing needs re-uploading.

### `make dev` could not be trusted to produce a working stack

Criterion 16 could not be *run*, let alone pass. Two consecutive `make dev` runs
from one terminal: the first healthy, then Ctrl-C, then

```
[dev] engine -> http://localhost:8000   ui -> http://localhost:3000
[ui] Failed to start server
[ui] Error: listen EADDRINUSE: address already in use :::3000
[engine] INFO:     Application startup complete.
```

Both URLs printed, the UI dead on the next line, the engine carrying on, exit
code 0. The browser was talking to an orphan from the first run. And in the
*healthy* run the editor loaded four times while uvicorn logged zero WebSocket
connections — the jobs panel said "Connecting to the engine…" indefinitely.

Five defects, all fixed, listed with what each actually was rather than what it
looked like.

**D1 — `dev.sh` did not kill its child process tree.** On Windows `npm run dev`
reaches `node` as a *grandchild*, so killing the pid the script recorded left the
process actually holding :3000 alive. The trap now walks the tree
(`taskkill //T //F` on the winpid), is idempotent, and does not return until both
ports are quiet — reclaiming, as a last resort, only ports preflight proved free
moments earlier and the script then bound itself. That last clause is what keeps
it inside the autonomy protocol: a port this script did not take is a port it
does not touch.

**D2 — the launcher reported success while half the stack was dead.** URLs were
printed before either process had bound anything. They are now printed only
after both ports accept a connection, so a URL on screen is an assertion the
script has checked. If either side exits at any point, the launcher names it,
tears the other side down and exits non-zero. `wait -n` was not used: it says
*a* job ended without saying which, and naming the one that died is the point.

**D3 — no preflight.** Both ports are now checked before anything starts. An
occupied port prints the owning PID and the exact reclaim command and exits
without starting anything. It does **not** kill that process — a stray
`taskkill` against a pid this script did not start is a destructive action
outside the repository.

**D4 — `/ws/jobs` never connected, and it was neither end of the socket.**
Diagnosed in headless Chrome against a real `make dev` stack before any code
changed. The client *did* call `new WebSocket(...)` on mount, with the correct
URL. The engine's per-route `Origin` check was correct and covered
`http://localhost:3000`. Neither ran, because the browser refused to send the
handshake:

```
Connecting to 'ws://localhost:8000/ws/jobs' violates the following Content
Security Policy directive: "connect-src 'self' http://localhost:8000
http://127.0.0.1:8000". The action has been blocked.
```

**A CSP `http:` source matches `http:` and `https:` URLs — never `ws:`.** Only
`'self'` carries the implicit ws upgrade, and the engine is not `'self'`; it is a
second origin on :8000. So the policy permitted every `fetch` to the engine and
silently blocked the one socket, before anything reached the network. That is why
uvicorn logged nothing at all, which is the detail that made this look like a
client that never opened the socket.

Two more instances of the same defect were sitting behind it: `img-src` and
`media-src` never named the engine origin either, so the thumbnail strip and the
proxy preview — both served from :8000 — were blocked too. Criterion 16 would
have hit those on its next screen. The port was hardcoded as well, while
`ENGINE_PORT` is configurable, so the policy went stale the moment anyone moved
the engine. All three directives now derive from one resolved engine origin, and
`lib/api/csp.test.ts` models the browser's own scheme-matching rule and asserts
the policy permits exactly what `lib/api/engine.ts` builds — with its own
negative control, that the shipped http-only policy must refuse the ws URL.

**D4b — and the socket would have stayed silent anyway.** Found while running
C5's negative control, and measured in Chrome rather than assumed: a WebSocket
refused by CSP is **constructed**, fires `error`, and lands in `readyState ===
CLOSED` **without ever firing `close`**. `useJobStream` set
`onerror = () => socket.close()` on the strength of "error is always followed by
close"; on an already-closed socket that is a no-op. So the hook stopped dead on
its first attempt — no retry, no state change, nothing reported — which is
exactly the indefinite "Connecting to the engine…" in the evidence. Had only the
CSP been fixed, this would have survived as a stream that gives up silently after
one failure, the first time anything refuses it. Failure is now recorded once per
socket from whichever event arrives, and `attempts` is published so a first
connection can be told from a retry.

**D5 — `/status` was green while the product could not function.** It reported
the engine version, the data directory, FFmpeg, libx264, CUDA and whether a
Gemini key was configured — every capability except the one that was broken.
`/health` gains `jobs_socket_ready` (route mounted at the path the UI asks for,
job worker alive, a WebSocket implementation available to uvicorn), and the
status page renders it with the same Yes/No treatment as "Can start video tools".
A **second** row opens the socket from the browser, through the same
`useJobStream` the editor uses, because the engine cannot see a browser refusing
the connection and that is the failure that happened. One row would have had to
pick a side; two rows tell the user which half is broken.

**A sixth, found while testing D1–D3:** `.env` shipping
`ENGINE_URL=http://localhost:8000` beat a shell-provided `ENGINE_PORT`, so the
engine listened on one port while the UI — and the CSP built from the same value
— pointed at another. The existing comment in `dev.sh` claimed this was fixed; it
was fixed only for the case where `.env` had no `ENGINE_URL` at all. A derived
value now follows the more specific source, and an `ENGINE_URL` naming a port
the launcher is not starting anything on is corrected with a named warning.
`dev.sh` also exports `NEXT_PUBLIC_ENGINE_URL` now, so one port setting reaches
the server fetches, the browser fetches and the policy alike.

### Two criteria that assert the assembled product

`.claude/rules/testing.md` says a prose criterion without an executable script is
a wish. The corollary this prompt kept paying for is narrower: **a criterion that
boots a component is a claim about the component.** Criteria 1–18 each boot one,
and each was green through all of the above.

- **19 — the launcher's own lifecycle.** Start, Ctrl-C (a real `SIGINT`, sent to
  the shell's own pid), assert both ports free, start again on the same ports
  with no EADDRINUSE. Then a squatted port refused by PID with nothing started.
  Then the UI killed mid-run, taking the engine down at a non-zero exit. Real
  `dev.sh`, real `next dev`, real uvicorn, scratch ports and a scratch
  `DATA_DIR`.
- **20 — the assembled stack.** `make dev`, a project created through the engine,
  the editor *and* `/status` opened in an installed Chrome over the DevTools
  protocol, and `/ws/jobs` observed completing its handshake with the jobs panel
  out of "Connecting…".

Criterion 20 asserts **positively** — the project's name on screen, "No jobs
running." in the panel — because the absence of "Connecting to the engine…" also
holds on the error card, and an earlier draft of it passed against a page reading
"No such project". It fetches `/status` as well as the editor because rendering a
Client Component from that Server Component answered HTTP 500 on the first
attempt: a render prop does not serialise across that boundary. Neither `next
build` (the page is `force-dynamic`, so the error only exists at request time)
nor any unit test can see that, and a gate that renders one page and calls the
app assembled has the same blind spot as the one that rendered none.

**Both negative controls were run, not assumed.** With `connect-src` put back to
http-only sources: criterion 20 exits 1 naming the CSP directive, and `/status`
shows its browser row red over a green engine row. The hook fix has its own
negative control too — reverting `onerror` to the shipped `socket.close()` fails
`components/jobs/jobStream.test.tsx`.

**No new dependency.** `scripts/cdp_browser.py` drives an already-installed
Chrome or Edge over the DevTools protocol — JSON over a WebSocket, through the
`websockets` that `uvicorn[standard]` already pulls in. Playwright would have
been a 300MB second Chromium for the same four messages. The trade is that
criterion 20 needs a browser on the machine and **fails, rather than skips, when
there is none**: a criterion that prints PASS without executing is the failure
the gate exists to prevent, and `REPCUT_BROWSER` is the escape hatch.

**One trap worth recording for anyone else scripting this repo from Python.**
`subprocess` on Windows resolves a bare `bash` through `CreateProcess`, which
searches `System32` *before* `PATH` — and `System32\bash.exe` is WSL's launcher.
The launcher therefore ran inside a Linux VM: it inherited none of the
environment passed to it, and its ports were a different network namespace, so
it started on the wrong ports and its preflight inspected the wrong machine.
`shutil.which` uses `PATH` and finds Git Bash; the two disagree, and only one of
them is the shell `make dev` uses. `dev_stack.bash_executable()` resolves it
explicitly.

## Assumed

| Area | Chose | Why |
|---|---|---|
| Partial-index dialect kwarg | `sqlite_where` only, no `postgresql_where` | SQLite is the only backend v1 ships against. A speculative Postgres kwarg would be config nothing has ever executed; Prompt 13 owns the migration path and can add it against a real database. |
| Naive datetime on write | Raise, not coerce | See above — a silently wrong hour is the failure being prevented. |
| `UTCDateTime` location | New `engine/repcut/db/types.py` | Column types are not models; `models.py` is already the longest file in the package. |
| Timestamp resolution / clock | `datetime.now(UTC)` via `utcnow()`, the only clock any column default reads | Not monotonic and not intended to be — these are wall-clock audit fields, not interval measurements. |
| Music library path | `$DATA_DIR/music/`, and the in-repo `data/music/` deleted | It was empty, and DATA_DIR now lives outside the repo. Two candidate music folders is a trap. Updated the four forward-looking references (`README.md`, `frontend-and-licensing.md`, the audio agent, the beat-and-audio skill); `docs/reports/prompt-00.md` left alone, being a historical record. |
| Proxy preset | `libx264 -preset veryfast -crf 23`, 720p ceiling, 30fps CFR | It is a scrubbing preview on the path between "upload finished" and "the user sees something", not a deliverable. Exports get `-preset slow -crf 18` per `ffmpeg.md`. NVENC was **not** used: the rule allows it for previews, but it would make proxy bytes depend on whether the machine has a GPU, and `params_version` cannot express that. |
| Proxy height | A ceiling, not a target — a 480p source stays 480p | Upscaling spends bytes inventing detail the camera did not capture. Rounded down to even, since x264 rejects an odd dimension under `yuv420p`. |
| Proxy audio | `aac 128k`, 48kHz, stereo, always | One project sample rate. Mixed rates desync on concat, and the fix has to be at ingest — by export it is too late. |
| Thumbnail strip | One tiled JPEG, `ceil(duration/2)` cells, 180px tall | One request for the scrubber and one `derived_artifacts` row, rather than N files the DB would have to enumerate. |
| Dry-run length | 2 seconds, on by default | Long enough to build the graph and open the encoder, short enough to be free. `render(dry_run_first=False)` exists for a caller that has already validated the plan. |
| Render timeout | 900s render, 60s probe | A guess, and the first one worth revisiting: it has never been measured against a multi-minute 4K clip on this laptop. Listed under Risks. |
| Shell regions drawn | Topbar, media library, preview, transfers, jobs. **No inspector, no timeline.** | The design system's layout has five regions; two of them hold scenes, beats and AI decisions, none of which exist before Prompt 03. An empty panel promising a feature is a dark pattern (P4), and a placeholder is a promise. They arrive with their content. |
| Uploads run serially | One transfer at a time, chained through a ref | Matches the engine's own worker, which is serial for the same reason: two encodes on a four-core laptop finish no sooner and make both progress bars lie. |
| Transfer cancel scope | Cancellable while queued, hashing or uploading — **not** during finalize | Finalize is the engine hashing and moving the assembled file. Aborting the request does not un-run it; it only costs the UI the answer. |
| Mutations are browser calls, not server actions | `client.ts` from the browser; `server.ts` only reads | A chunked 2GB transfer routed through the Next server copies every byte through a second process and blows the budget criterion 13 measures. Progress, cancel and resume do not survive that round trip either. |
| Library refresh | Refetch keyed on terminal job transitions plus a transfer counter | Depending on the raw job list would refetch on every progress tick. Keying on "a job of this project reached a terminal state" refetches exactly when the library could have changed. |
| Job cancel is reachable from any page | Accepted, no extra check | The browser-tab question from `security.md`: the worst a hostile page can do is stop a job the user restarts with Re-ingest, and job ids are random UUIDs. Recorded rather than assumed away. |
| Criterion 13's fixture | 2GB of `rawvideo`, not an encode | Equally real to the upload path, and a disk write rather than half an hour of x264. |
| SKIP as a third gate verdict | Prints `[SKIP]` with the reason, counted apart, does not fail the run | Amendment 004 §3 asks for exactly this. It is deliberately **not** a PASS: a criterion that prints PASS without executing is the sync-guard failure this report already has one instance of. |

## Deviations from the guide

All material deviations are
[amendment 004](../guide-amendments/004-prompt-02-fixtures-paths-scope.md), which
was written and accepted before implementation started. §7 of it has been
extended in place with the resume-lookup clause above — the amendment has never
reached `main`, so it is amended rather than superseded.

`0001_initial_schema` is likewise amended in place rather than followed by an
`0002`: it exists only on this branch and no database anywhere has ever run it.

## Open issues

- **A green signal on top of a broken product, four times, and it is one flaw.**
  Not four bugs — one habit. Listed in the order they were found:

  1. `verify-02` 20/21 green while every upload 500'd (the Windows event loop).
  2. `make dev` printing both service URLs while the UI was already dead.
  3. `/status` all-green while `/ws/jobs` never connected.
  4. `next build` green, 180 vitest tests green, and `/status` answering HTTP
     500 in a browser — a Server/Client Component boundary that only exists at
     request time on a `force-dynamic` page.

  Each individually is small. Together they say something specific: **this
  project verified components and reported on components, and nothing ever
  asserted that the assembled thing worked.** Every criterion booted a piece and
  checked the piece. A gate built that way cannot fail for the reasons a user
  fails, and every one of these was found by a person, late, after a green run.

  Criteria 19 and 20 are the structural answer rather than three more patches:
  19 drives the launcher's own lifecycle, 20 starts `make dev` and opens the app
  in a real browser. That single assertion in 20 would have caught 1, 2, 3 and 4.
  **The rule to carry forward: every prompt from here owes at least one
  criterion that starts the product the way a person starts it and asserts
  something a person would notice.** Prompt 03's Playwright layer is where this
  gets a proper home; until then, 19 and 20 are it — and neither may be weakened
  to make a build green.

  The corollary is a cost, and it is worth naming: criteria 19 and 20 take about
  three minutes between them and need a Chromium-family browser on the machine.
  That is the price of a gate that can fail the way the product fails, and it is
  cheaper than the two evenings this cost.
- **Criterion 16 caught what twenty automated criteria could not, and that is
  the finding worth keeping.** The first real-footage run failed on *every*
  upload — the engine `make dev` starts was on an event loop with no subprocess
  transport, so no FFmpeg or ffprobe call could run at all. **51 tests spawn
  FFmpeg for real** (counted: test functions taking `make_clip` or
  `upload_clip`) and every one of them passed, because pytest-asyncio builds its
  loop from the default policy (Proactor) while uvicorn built the server's from
  `--reload` (Selector). The suite was never on the loop the server was on. The
  gate proved the *module* worked and never proved the *application* worked. Fixed, and pinned by criteria 17 and 18 — but the
  general lesson is not fixed by two criteria: **every gate here measures a
  configuration, and the configuration it measures has to be the one that
  ships.** Anything a gate boots differently from `make dev` is untested by
  definition. Prompt 03's Playwright layer is the next place this can recur.
- **The loop check is a warning at startup, not a refusal.** Booting the engine
  through a hand-written `python -m uvicorn … --reload` line still selects the
  broken loop; the engine logs an ERROR, `/health` reports
  `event_loop_can_spawn: false`, and uploads fail with a named 503 instead of a
  traceback. That is deliberate — the UI needs a reachable engine to render the
  gap — but it means the guarantee lives in the entry point, and the entry point
  can be bypassed. Criterion 17 asserts `dev.sh` has not been edited back.
- **`UnexpectedErrorBoundary` re-raises once a response has started**, which
  reaches uvicorn's own logger and prints an unredacted traceback to the
  console. It cannot be both signalled and silent, and the scrubbed copy is
  already written by then; no response body is affected. Recorded rather than
  hidden.
- **Refcounting and orphan GC are owed to Prompt 12**, per amendment 004, and
  the deferral ends early if any prompt before 12 ships a delete or remove
  surface — a "remove clip" action, a project delete, an export cleanup. Prompt
  02 ships no delete endpoint, so nothing can be orphaned yet. The cascade rules
  that GC will depend on are already asserted
  (`test_deleting_a_project_leaves_the_blob_orphaned`).
- **Criterion 16 is unticked.** Every box in `docs/manual-checks/prompt-02.md`
  is still open, so the gate exits 1 — correctly. The automated criteria only
  ever prove the synthetic fixtures work; nothing here has met real phone
  footage. **This is what the prompt needs a human for**, and it is the only
  thing standing between the branch and `/gate 02`.
- ~~Track B is not started.~~ **Closed.** Criteria 10–13 are executable checks.
- ~~The resume lookup is served but has no client.~~ **Closed.**
  `lib/upload.ts` looks up `(project_id, sha256)` before opening a transfer, and
  two tests cover the resumed and the fresh path.
- ~~Nothing has been driven through a browser.~~ **Partly closed.** Criterion 20
  now opens the editor and `/status` in a real Chrome against a real `make dev`
  stack and asserts the jobs socket connects, so the socket delivering events end
  to end is no longer pinned only by unit tests. Two things still are: the proxy
  actually playing and seeking through Range requests, and a real file surviving
  the drag-and-drop round trip — jsdom has neither a media pipeline nor
  drag-and-drop. `docs/manual-checks/prompt-02.md` is where those get signed off,
  and Prompt 03 is where Playwright arrives (`.claude/rules/testing.md`'s E2E
  layer). Criterion 20's CDP client is deliberately minimal and should be
  retired in its favour, not grown.
- **Job cancel has no UI beyond the button.** A cancelled job disappears from
  the active list; there is no "cancelled by you" state distinct from a failure,
  and no undo. Fine while every job is idempotent and re-runnable with
  Re-ingest; worth revisiting when a job produces something a user chose.

## Dependency licence audit (this prompt's additions)

Repcut is AGPL-3.0. Versions and licences read from installed package metadata,
not from memory. `greenlet` and `Mako` are transitive (SQLAlchemy's asyncio
bridge and Alembic's template engine) and are listed because they are linked in.

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| sqlalchemy[asyncio] | 2.0.51 | MIT | yes |
| aiosqlite | 0.22.1 | MIT | yes |
| alembic | 1.19.0 | MIT | yes |
| greenlet (transitive) | 3.5.4 | MIT AND PSF-2.0 | yes |
| Mako (transitive) | 1.4.1 | MIT | yes |

Added since, both test-only (the engine's `dev` extra) and neither imported by
any runtime module:

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| psutil | 7.2.2 | BSD-3-Clause | yes |
| types-psutil | 7.2.2.20260518 | Apache-2.0 | yes |

`python-multipart`, which amendment 004 anticipated for the upload endpoint, was
**never needed and is not installed**: the chunked endpoint streams a raw
request body rather than parsing a multipart form, so the dependency has no call
site. No model weights added.

UI dependencies changed by the security review — versions bumped, no new
package added, licences unchanged from what they replaced:

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| next | 14.2.35 → 16.3.0 | MIT | yes |
| eslint | ^8 → 9.39.5 | MIT | yes |
| eslint-config-next | 14.2.35 → 16.3.0 | MIT | yes |

UI dependencies added by Track B. `axe-core` is **MPL-2.0**, which is
file-level copyleft and GPL/AGPL-compatible by the FSF's own reading; it is a
devDependency, used only by the test suite, and is not linked into any shipped
bundle:

| Package | Version | Licence | AGPL-3.0 compatible |
|---|---|---|---|
| hash-wasm | 4.12.0 | MIT | yes |
| server-only | 0.0.1 | MIT | yes |
| axe-core (dev) | 4.13.0 | MPL-2.0 | yes |
| @testing-library/react (dev) | 16.3.2 | MIT | yes |
| @testing-library/jest-dom (dev) | 6.9.1 | MIT | yes |
| @testing-library/user-event (dev) | 14.6.3 | MIT | yes |
| jsdom (dev) | 27.4.0 | MIT | yes |
| vitest (dev) | 4.1.10 | MIT | yes |
| @vitejs/plugin-react (dev) | 6.0.5 | MIT | yes |

Both fonts are SIL OFL 1.1 (Sora, IBM Plex Sans), committed as latin-subset
woff2 with provenance in `ui/app/fonts/README.md`. OFL permits embedding and
redistribution; neither is renamed, and neither is sold on its own.

## Gate status

`make verify-02`: **FAILED: 1 of 25 criteria** — and the one is criterion 16,
the human check, which no amount of code can turn green. Every automated
criterion passes with a measured value beside it (re-run in full after the
dev-launcher and jobs-socket fixes; every number below is from that run).

The denominator moved from 23 to 25 because criteria 19 and 20 were added. No
existing criterion was weakened or removed to make room — the count of criteria
that can fail went up, which is the only direction it should ever move.

| Criterion | Result |
|---|---|
| 1 migrations round-trip + schema | PASS — 3 alembic steps, 6/6 tables, unique key present |
| 2 ffmpeg_builder snapshots / no `shell=True` / no user path logged | PASS — 63 tests, 0 shell call sites, argv redacted |
| 3 non-video rejected, no rows written | PASS — `unsupported_media_type`, `not_a_video`, `media_files=0` |
| 4 resume across a kill (idempotent) | PASS — killed at 883731B, resumed from 883731B, references=1 on both runs |
| 5 duplicate links, re-encodes nothing | PASS — 1 blob on disk, references=2, ingest jobs 1 → 1 |
| 6 VFR source → CFR proxy, drift budget | PASS — `30/1,13/1` → `30/1,30/1`, A/V drift 17.1ms against a 40ms budget |
| 6b unknown container stores NULL not false | PASS — matroska heuristic says constant, stored `None` |
| 7 stored resolution is display resolution | PASS — coded 1280x720 rotation 90 → stored 720x1280 |
| 8 strip cells, proxy, duration, audio | PASS — h264 720p 5.01s 48000Hz; strip 960x180 = 3 cells |
| 9 `/ws/jobs` queued → running → succeeded | PASS — monotonic progress 0.0 → 1.0 across 5 named steps |
| 9b failure carries a cause, not a traceback | PASS — "this clip's file is missing from the media library" |
| 10 UI clean and builds | PASS — tsc, eslint, `next build`, 4 routes |
| 10 zero `any` in `ui/` | PASS — 0 occurrences |
| 11 tokens are the only source of style | PASS — 0 ad-hoc colours outside `globals.css` |
| 12 accessibility baseline | PASS — 183 vitest tests, axe run in every component directory |
| 13 large-file memory (2GB, RSS < 500MB) | PASS — **peak 355MB, baseline 90MB**, 2.00GB in 8MB chunks |
| 14 verify-01 still green (no regression) | PASS — 13 of 13 |
| 15 nothing forbidden tracked | PASS — 0 files |
| 16 [HUMAN] real phone footage | **FAIL — 6 unticked boxes.** Correct, and the only thing left. |
| 17 dev configuration: finalize + ingest | PASS — `dev.sh -m repcut`, `ProactorEventLoop` can_spawn=True, finalize ok, ingest `['succeeded']`, references=1 |
| 18 no path or traceback in a finalize body | PASS — 5 finalize bodies read raw off the wire, 0 offending |
| 19 `make dev`: port hygiene and loud failure | PASS — restart ok (both ports free after Ctrl-C, second run clean), occupied-port ok (non-zero exit naming the PID, nothing started), half-death ok (UI killed → engine torn down, non-zero exit) |
| 20 assembled stack: `make dev`, browser, `/ws/jobs` | PASS — ports up, editor rendered, **jobs socket accepted**, 0 CSP violations, panel connected, `/status` agrees |

Criterion 13 has been run four times and reported **331 / 346 / 347 / 355MB**.
Quoted as a set rather than averaged: four samples of the same thing on the same
machine, and the spread is what a single number would hide.

**The `any` check was missing until the criteria were re-read against the
gate.** Criterion 10's text names four commands *and* "zero `any` in
`ui/**/*.{ts,tsx}`"; the gate ran the commands and never checked the second
half. `tsc --strict` does not cover it — `strict` rejects an *implicit* any and
says nothing about a written one, so `catch (e: any)` typechecks cleanly and
defeats `code-style.md` entirely. The tree was clean, so nothing was broken;
what was broken was the gate's claim to have checked. It is a scan now, matched
in type position only and verified against a planted `any`. `npm run test`, the
fourth command criterion 10 names, is measured at criterion 12 rather than run
twice.

Supporting measurements, all run on this machine:

| Check | Result |
|---|---|
| `pytest engine -m "not gpu"` | **288 passed** |
| `pytest -m slow` (criterion 13) | 1 passed, 3m29s |
| `ruff check` / `ruff format --check` | clean, 49 files |
| `mypy --strict` | clean, 48 source files |
| UI `eslint . --max-warnings 0` / `tsc --noEmit` | both clean |
| UI `vitest run` | **183 passed**, 16 files |
| `next build` | clean, 4 routes |
| `npm audit --omit=dev --audit-level=high` | **1 high — `nanoid <3.3.18`, see below** |

**The npm audit went red between sessions, and it is a merge blocker.**
`nanoid` is transitive — `next@16.3.0 → postcss@8.5.23 → nanoid@3.3.17` — and the
advisory (GHSA-2v37-7h3g-55p8, custom generators loop indefinitely when size is
zero) was published after the last clean run. `npm audit fix` resolves it
without touching a direct dependency. It is **not** fixed in this session's
changes because it is unrelated to them and nothing here should quietly bump a
lockfile; it is recorded here so it is fixed by upgrading rather than by an
ignore entry (`security.md`). CI's `npm audit --omit=dev --audit-level=high` job
will fail until it is.

`make test-gpu`: **not run, and not applicable** — nothing in this prompt so far
touches CUDA, and no `@pytest.mark.gpu` test exists. It stays that way while the
proxy is x264: see the NVENC decision under *Assumed*.

## Risks / known gaps

- **The sync guard is one warning, not a refusal.** It fires reliably now, but a
  warning in a JSON log stream during startup is easy to miss, and nothing in
  `/health` reports it — so the UI cannot surface it either. If footage sitting
  in OneDrive is a P4 violation worth stopping for, the guard should be a
  `/health` field that Track B renders. Flagged rather than decided — though the
  stated cost was wrong: adding `event_loop` / `event_loop_can_spawn` showed that
  `verify_01.sh` criterion 4 asserts the *presence and types* of its ten named
  fields, not an exact field set, so a new field costs a schema line and a
  fixture line, not a gate rewrite.
- **The guard is name-and-env based, so it is not exhaustive.** It knows
  OneDrive, Dropbox, Google Drive and iCloud by folder name, plus OneDrive's own
  environment variables. A renamed Dropbox folder, a sync client not on the list,
  or a mapped network drive that syncs elsewhere all pass clean.
- **The partial index closes the duplicate-session hole; it does not implement
  resume.** Without the endpoint that looks the session up and returns its
  offset, the index turns a silent orphan into a visible `IntegrityError`. That
  is strictly better, but the error is only useful once something handles it.
- **`UTCDateTime` fixes reads through the ORM only.** Raw SQL — of which the
  tests already contain a fair amount — still returns whatever the driver
  returns. Any future code doing `text("SELECT created_at …")` and comparing the
  result is back in the original trap.
- **No test asserts the partial index is actually *used* by the query planner.**
  It is unique, so correctness does not depend on the plan, but the "lookup path"
  claim is untested until a query exists to test.
- **The render timeout is a guess.** 900s has never been measured against a
  multi-minute 4K clip on this laptop. Too low turns a slow encode into a
  `FFmpegTimeoutError` the user reads as a failure; too high wedges a job slot.
  It wants a measurement during the ingest deliverable, not a bigger number.
- ~~Rotation is asserted only indirectly.~~ **Closed.** `conftest` now stamps a
  real display-matrix tag with `-display_rotation`, so the fixture carries the
  same `side_data_list: [{"rotation": 90}]` a phone writes rather than a
  simulated one. Criterion 7 measures coded 1280x720 → stored display 720x1280
  through it.
- **The stderr classifier is a substring match against one FFmpeg version.**
  Patterns came from ffmpeg 8.1 locally; CI runs whatever `apt` ships. A
  phrasing change degrades a specific error to the generic
  `FFmpegEncodeError` — recoverable, but it makes the UI message vaguer without
  anything failing to say so.
- ~~`-progress` is not wired.~~ **Closed.** `-progress pipe:1 -nostats` and its
  parser landed with the ingest job; criterion 9 measures monotonic progress
  0.0 → 1.0 across five named steps.
- ~~Nothing yet writes to five of the six tables.~~ **Closed.** All six now have
  a production writer, and the column shapes are validated against a working
  pipeline rather than against the amendment.
- **The stderr classifier and the security allow-lists share a failure mode: a
  substring match against one environment.** The classifier is pinned to ffmpeg
  8.1's wording; `is_allowed_origin` is exact-match precisely because the
  substring version let `http://localhost:3000.evil.example` through, and that
  case is now a test. The classifier has no equivalent test against another
  FFmpeg build, because CI runs whatever `apt` ships and nothing asserts which.
- **The `S` ruleset is on but `pip-audit` has never failed here.** The CI job is
  new and has only ever run against a clean tree, so its failure path — the
  thing it exists for — is unexercised. The first real advisory is also the
  first test of whether the job reports usefully.
- **Criterion 13 measured this machine, three times.** 331 / 346 / 347MB are
  peaks on an idle laptop with an NVMe disk, uploading synthetic raw video over
  loopback in a single process. A slower disk changes how long the write buffer
  is held, and a real browser adds a second process the number does not include.
  The budget has headroom (347 of 500MB), but three samples are not a
  distribution, and the 16MB spread across them is unexplained.
- **The criteria were re-read against the gate once, and found one gap.** The
  `any` clause of criterion 10 had never been executed. That was found by
  reading the prompt's success criteria line by line against
  `verify_02.sh` — not by any check, because nothing checks that a gate
  implements the criteria it claims to. Criteria 11 and 12 were re-read the same
  way and hold: 12's contrast is recomputed from the parsed tokens against
  4.5:1, and `prefers-reduced-motion` is asserted in `lib/tokens.test.ts`.
- **The 8MB chunk size is fsync'd per chunk.** That is what makes resume
  correct, and it is also the dominant cost of a 2GB transfer — the upload phase
  is disk-sync bound, not CPU bound. Nobody has measured whether a larger chunk
  or a batched sync would halve the time without weakening the resume guarantee.
- **`jsdom` is not a browser.** Every UI test runs in a DOM with no media
  pipeline, no WebSocket and no drag-and-drop: `HTMLMediaElement.play`, the
  socket and `DataTransfer` are all stand-ins declared in `test/setup.ts` and in
  the test files. They are honest about being stand-ins, but the three things
  they stand in for are exactly the three the UI depends on most.
- **The socket contract is pinned by two tests that must be edited together.**
  `test_the_socket_payload_carries_the_fields_the_ui_parses` and
  `schemas.test.ts` assert the same field list from opposite sides. Renaming a
  field fails one of them — but someone who renames it in the engine *and*
  updates the engine's test still leaves the UI silently broken until CI runs
  the other suite. Both name each other, which is the mitigation available
  without generating one from the other.
- **`useJobStream` reconnects forever.** Backoff caps at 8s and never gives up,
  which is right for a local engine that gets restarted by hand and wrong if the
  engine is gone for good: the panel says "Reconnecting…" indefinitely rather
  than eventually saying "not running, start it".
- **Nothing enforces that a new component gets an axe test in its own
  directory** except criterion 12's directory scan, which is a spelling check on
  file layout rather than a coverage measurement. A test file containing
  `axe.run` on one trivial element satisfies it.
