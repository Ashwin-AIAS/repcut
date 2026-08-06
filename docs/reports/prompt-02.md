# Prompt 02 — Media pipeline & design system
Branch: prompt-02 · Gate: NOT RUN (`verify_02.sh` not authored yet) · Date: 2026-08-06

**Status: in progress.** Track A of the two-track split in
[amendment 004](../guide-amendments/004-prompt-02-fixtures-paths-scope.md) is
part-built: the amendment, the sync-root guard, the schema with its first
migration, and `ffmpeg_builder.py` exist. The chunked upload endpoint, the
ingest job and `/ws/jobs` do not. This report is written as the work lands
rather than at the end, so what is recorded below is what is true today.

## Built

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
- 91 engine tests, all CPU.

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

## Deviations from the guide

All material deviations are
[amendment 004](../guide-amendments/004-prompt-02-fixtures-paths-scope.md), which
was written and accepted before implementation started. §7 of it has been
extended in place with the resume-lookup clause above — the amendment has never
reached `main`, so it is amended rather than superseded.

`0001_initial_schema` is likewise amended in place rather than followed by an
`0002`: it exists only on this branch and no database anywhere has ever run it.

## Open issues

- **Refcounting and orphan GC are owed to Prompt 12**, per amendment 004, and
  the deferral ends early if any prompt before 12 ships a delete or remove
  surface — a "remove clip" action, a project delete, an export cleanup. Prompt
  02 ships no delete endpoint, so nothing can be orphaned yet. The cascade rules
  that GC will depend on are already asserted
  (`test_deleting_a_project_leaves_the_blob_orphaned`).
- **`verify_02.sh` does not exist.** Until it does, "Prompt 02 works" is a
  claim, not a measurement. It owns 16 criteria including the `[HUMAN]` manual
  check that cannot pass on its own.
- **`docs/manual-checks/prompt-02.md` does not exist.** Criterion 16 exits 1
  while any box in it is unticked; the automated criteria only ever prove the
  synthetic fixtures work.

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

`python-multipart` and `psutil`, which amendment 004 anticipates for the upload
endpoint and the 2GB memory criterion, are **not installed yet** and are not
audited here. No model weights added.

## Gate status

`verify_02.sh` is not authored, so there is no Prompt 02 gate result to report.
What has been measured:

| Check | Result |
|---|---|
| `pytest engine -m "not gpu"` | 91 passed |
| `ruff check` / `ruff format --check` / `mypy --strict` | 3/3 exit 0, 22 source files |
| Migration round-trip (upgrade → downgrade base → upgrade) | PASS |
| Model/migration drift (`compare_metadata`) | PASS, no differences |
| `params_version` binding, both failure directions | PASS (measured, table above) |
| VFR source → CFR proxy, against real FFmpeg | PASS (`30/1` both rates out) |
| `bash scripts/verify_01.sh` (no regression) | **PASSED: 13 of 13** |

`make test-gpu`: **not run, and not applicable** — nothing in this prompt so far
touches CUDA, and no `@pytest.mark.gpu` test exists. It stays that way while the
proxy is x264: see the NVENC decision under *Assumed*.

## Risks / known gaps

- **The sync guard is one warning, not a refusal.** It fires reliably now, but a
  warning in a JSON log stream during startup is easy to miss, and nothing in
  `/health` reports it — so the UI cannot surface it either. If footage sitting
  in OneDrive is a P4 violation worth stopping for, the guard should be a
  `/health` field that Track B renders. Flagged rather than decided, because
  adding a field changes the ten-field `/health` contract that `verify_01.sh`
  criterion 4 asserts.
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
- **Rotation is handled by trusting FFmpeg's auto-rotate, and asserted only
  indirectly.** The builder never reads the container's dimensions — it takes
  the probed display height — but no test yet feeds a clip carrying a real
  rotation side-data tag, because `lavfi` does not produce one. Writing the tag
  onto a synthetic clip is possible and belongs with the ingest deliverable;
  until then the portrait path is covered by argv assertions, not by pixels.
- **The stderr classifier is a substring match against one FFmpeg version.**
  Patterns came from ffmpeg 8.1 locally; CI runs whatever `apt` ships. A
  phrasing change degrades a specific error to the generic
  `FFmpegEncodeError` — recoverable, but it makes the UI message vaguer without
  anything failing to say so.
- **`-progress` is not wired.** `/ws/jobs` needs per-frame progress, which means
  `-progress pipe:1 -nostats` and a parser. Deliberately not added ahead of its
  consumer, but it will change the runner's shape when it lands.
- **Nothing yet writes to five of the six tables.** `media_blobs`,
  `media_files`, `derived_artifacts`, `upload_sessions` and `jobs` have
  constraints proven by tests and no production writer. Column shapes are
  therefore validated against the amendment, not against a working pipeline.
