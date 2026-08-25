# Amendment 004 — Prompt 02: fixtures, paths, scope, and a content-addressed media store
Date: 2026-08-05
Affects: Section 4 (Architecture — Storage), Prompt 02 (Deliverables 1, 2, 3, 5;
Constraints; Autonomy Protocol; Success Criteria), Prompt 12 (Deliverables —
inherits refcounting and orphan GC)
Status: ACCEPTED

## What the guide says

Section 4, *Stack decisions*:

> **Storage:** local filesystem (`data/projects/{id}/`), SQLite for metadata (no
> Postgres in v1 — single user; migration path documented).

Prompt 02, Deliverables — abridged to the clauses this amendment touches:

> 1. SQLite schema (SQLAlchemy async + Alembic): `projects`, `media_files`
>    (path, hash, duration, resolution, fps, codec, created_at), `jobs` (type,
>    status, progress, error)
> 2. … chunked upload endpoint writing to `data/projects/{id}/source/` —
>    resumable, hash-verified, rejects non-video MIME types with named errors
> 3. Ingest job per uploaded file: ffprobe metadata extraction, thumbnail strip
>    (1 frame / 2s), proxy preview generation (720p H.264) … all via
>    `engine/ffmpeg_builder.py`
> 5. `ui/` design system — **the premium foundation every later prompt
>    inherits**: design tokens (color scale, spacing, type ramp …)

Prompt 02, Constraints:

> - Duplicate upload (same hash) links to existing file — no double storage

Prompt 02, Autonomy Protocol:

> Fully autonomous. UI layout/styling decisions are Claude Code's own (dark
> theme, minimal); note choices in report.

Prompt 02, Success Criteria:

> - Upload 3 real phone clips (incl. one HEVC/VFR) → library shows correct
>   metadata, thumbnails, playable smooth proxies
> - Kill engine mid-upload, restart, resume succeeds (idempotency)
> - 2GB file uploads with engine RSS staying < 500MB

## What we found

Seven places where Prompt 02 collides with a binding rule in `.claude/rules/`,
with the repo as Prompt 01 left it, or with itself.

| # | Collision | Resolution |
|---|---|---|
| 1 | Success criterion 1 requires three real phone clips; `testing.md` and `git-and-ci.md` forbid committing media at all | Synthetic-fixture gate, plus a human-signed manual checklist the gate enforces |
| 2 | Three paths for one module: guide `engine/ffmpeg_builder.py`, `ffmpeg.md` `engine/media/ffmpeg_builder.py`, actual package `engine/repcut/` | `engine/repcut/media/ffmpeg_builder.py` |
| 3 | "2GB file, RSS < 500MB" cannot run in CI or in the fast test loop | `@pytest.mark.slow`, disk-gated, file generated at test time, `psutil` for RSS |
| 4 | Deliverable 5 says invent a design system and the Autonomy Protocol hands styling to Claude Code; `code-style.md` already binds `ui/` to `.claude/skills/repcut-design-system` | The skill is the source of truth; Prompt 02 implements it |
| 5 | Scope: schema + migrations + resumable upload + builder + WebSocket jobs + design system + four UI surfaces, on one branch | One branch, two tracks, hard checkpoint after gate criteria 1–8 |
| 6 | Deliverable 2 puts bytes under `data/projects/{id}/source/`, but the Constraints require a duplicate to link rather than re-store. Across two projects, both cannot hold | Content-addressed store under `$DATA_DIR/media/`; project folders hold references and project-scoped output only |
| 7 | "resumable" and "kill engine mid-upload, restart, resume succeeds", but none of the three tables holds in-flight transfer state | `upload_sessions` table — durable offset, reconciled against the `.part` file on disk, found by a partial unique index rather than by an id the caller may have lost |

Collisions 6 and 7 are structural: they change the schema and the on-disk
layout every prompt from 03 to 13 reads. The other five change process, paths
and gate design.

## Why the guide's version doesn't work

**1 — Real footage cannot be a gate input.** `testing.md`: "Never commit
media." `git-and-ci.md`: "No real footage in the repo… Never commit a `.mp4`,
even a tiny one." The repository is public, and P4 says footage never leaves
the machine — committing a gym clip breaks both. A criterion that cannot be
executed by `make verify-02` is, per `testing.md`, "a wish", not a gate. But
the criterion is testing something real that synthetic fixtures cannot reach:
`ffmpeg -f lavfi` produces neither HEVC-from-a-phone nor a real VFR cadence nor
a rotation tag written by a phone camera app.

**2 — The builder path in the guide does not exist.** There is no `engine/`
Python package; Prompt 01 created `engine/repcut/`. The guide's
`engine/ffmpeg_builder.py` and `ffmpeg.md`'s `engine/media/ffmpeg_builder.py`
are both unimportable as written. Picking either literally would put a module
outside the installed package.

**3 — The memory criterion cannot run where the guide implies.** CI runners
have neither the disk nor the minutes for a 2GB transfer, and `testing.md`
defines a fast loop that a multi-minute test destroys. `resource.getrusage` is
POSIX-only; this is a Windows machine, so RSS needs `psutil`. A criterion
excluded from CI and from `make test` must still be *run and reported* by the
gate, or "RSS < 500MB" is an unverified claim.

**4 — Two design systems is one too many.** `code-style.md` binds `ui/` to
`.claude/skills/repcut-design-system` ("no component libraries — the design
system is ours"), and `frontend-and-licensing.md` requires tokens to live in
one place and be "the only source of style". Deliverable 5 and the Autonomy
Protocol together invite Claude Code to invent a parallel set. Whichever ran
last would win, every prompt from 03 to 13 would inherit the invented one, and
the skill would become a document nobody reads.

**5 — One session cannot hold the whole prompt.** The deliverable list is the
largest in the guide and the engine half is what every later prompt imports. An
interrupted session that stopped halfway through the UI leaves the engine
half-migrated and unverifiable.

**6 — "Store under the project" and "duplicates do not double-store" are
mutually exclusive.** Concretely: clip X uploads to project A and lands at
`data/projects/A/source/X.mp4`. The same clip uploads to project B. To honour
"no double storage", B's row must point into A's folder. Three consequences,
all silent:

- Deleting or moving project A breaks project B, and nothing in project B says
  why.
- Which project owns the bytes is decided by upload order — an invisible fact
  with no recovery path once folders exist.
- The gate's measurable form of this constraint ("byte count on disk unchanged
  after the second upload") can only pass by writing that cross-project
  reference, so the gate would *enforce* the fragility.

Derived artifacts make it worse. A proxy is a pure function of (source bytes,
encode recipe). Under the guide's layout, project B either re-encodes an
identical 720p proxy — dedup that saves the 4GB source and then spends the
encode anyway, once per project — or points into A's folder and inherits the
same breakage one level deeper. And when a later prompt changes the recipe
(Prompt 04 renders graded proxies per scene; any prompt may change resolution,
CRF or colour flags), the existing file keeps its path and quietly means
something different. There is no invalidation path at all.

Content addressing is also what the rules already require elsewhere:
`gemini-usage.md` mandates a cache keyed `(video_hash, scene_id,
prompt_version)`. Analysis is keyed by the bytes; storage keyed by the project
would put the filesystem and the cache on two different identities of the same
clip.

**7 — Resume without durable state is guesswork.** After `SIGKILL` the engine
restarts holding a `.part` file and no memory of the declared total size, the
chunk size the client used, which project and display name the transfer
belonged to, or how many bytes were durably flushed rather than buffered. It
can `stat` the file, but a `stat` cannot distinguish "complete" from "truncated
at a chunk boundary", and it cannot verify a hash it was never told. Resume
then means trusting a client-claimed offset: a replayed chunk appends duplicate
bytes, and the corruption surfaces only as a hash mismatch at the end of a
multi-GB transfer. Success criterion 4 additionally requires the whole
criterion to be *idempotent* — re-running leaves the same DB state — which
needs a row saying "this transfer, this offset, this target", not a file
whose length is the only evidence.

And durable state the caller cannot find is not resumable either. Keying every
read on the session id makes resume a property of the *client's* memory: the
gate's client is a test harness holding the id across the kill, so criterion 4
would certify resume on the strength of a caller that cannot forget. The
browser can. Resume needs a key derived from what the client can always
re-state — the project and the content hash — not from a handle it may have
dropped.

## Proposed change

### 1 — Fixtures split

**Amend Prompt 02, Success Criteria.** Replace:

> ~~Upload 3 real phone clips (incl. one HEVC/VFR) → library shows correct
> metadata, thumbnails, playable smooth proxies~~

with two criteria:

> - **Automated.** Against fixtures generated at test time by a `conftest.py`
>   factory (`ffmpeg -f lavfi`), including a deliberately VFR clip and a clip
>   with a rotation side-data tag: metadata extracted matches ffprobe, the
>   proxy is CFR with end-of-clip A/V drift < 40ms, stored resolution is the
>   *display* resolution, and the thumbnail strip has `ceil(duration / 2)`
>   frames. (Gate criteria 6, 7, 8.)
> - **`[HUMAN]`.** `docs/manual-checks/prompt-02.md` exists and contains no
>   unticked boxes. While any box is unticked the gate prints
>   `[HUMAN] real phone footage unverified` and **exits 1**. It never passes on
>   its own. (Gate criterion 16.)

### 2 — Builder path

**Amend Prompt 02, Deliverable 3:** `engine/repcut/media/ffmpeg_builder.py`.

`ffmpeg.md`'s `engine/media/ffmpeg_builder.py` is read as package-relative —
the rule's binding content is "one module builds every invocation, arguments
are a `list[str]`, never `shell=True`", and that is honoured exactly. The rule
file is not edited; this amendment records the reading.

### 3 — Large-file memory criterion

**Amend Prompt 02, Success Criteria.** The 2GB/RSS criterion becomes gate
criterion 13: `@pytest.mark.slow`, skipped when free disk < 5GB or
`REPCUT_SLOW=0`, generating the file at test time and deleting it, sampling
engine RSS with `psutil`, asserting peak < 500MB and printing it. Excluded from
CI and from `make test`. The gate runs it and reports `SKIPPED` **with the
reason** rather than passing silently.

### 4 — Design system ownership

**Amend Prompt 02, Autonomy Protocol:**

> ~~UI layout/styling decisions are Claude Code's own (dark theme, minimal)~~
> **UI layout and styling are constrained by `.claude/skills/repcut-design-system`,
> which is the source of truth for tokens, editor shell layout, job states and
> the AI-suggested control. Prompt 02 implements the skill; it does not invent a
> parallel token set. Where the skill gives a range rather than a value (the
> accent hue), choose, record the value and its measured contrast ratio in the
> report, and amend the skill in the same commit.**

This is the only narrowing of the guide's autonomy protocol in Prompt 02.

### 5 — Sequencing

**Add to Prompt 02, Constraints:** one branch `prompt-02`, two tracks with a
hard checkpoint between them. Track A (engine): amendment 004 → schema and
migrations → `ffmpeg_builder` and its snapshot tests → chunked resumable upload
→ ingest job → `/ws/jobs`, ending with gate criteria 1–8 green and
`/checkpoint` run. Track B (UI): tokens → shared components → editor shell →
dashboard, upload, library grid, proxy player, criteria 9–13.

### 6 — Content-addressed media store

**Amend Section 4, Storage:**

> ~~**Storage:** local filesystem (`data/projects/{id}/`)~~ **Storage:** local
> filesystem under `$DATA_DIR`. Media bytes are content-addressed by SHA-256 in
> `$DATA_DIR/media/`; `$DATA_DIR/projects/{id}/` holds project-scoped metadata
> and rendered output, never source bytes. SQLite for metadata.

**Amend Prompt 02, Deliverable 2:** the chunked upload endpoint writes to
`$DATA_DIR/uploads/` while in flight and moves the completed file into the
content-addressed store on finalize. On-disk layout:

```
$DATA_DIR/
  media/
    blobs/<sha[:2]>/<sha>/source<ext>
    derived/<sha[:2]>/<sha>/<artifact_kind>/<params_version>/…
  projects/<project_id>/                 # project-scoped output; no source bytes
  uploads/<upload_session_id>.part       # in-flight only
  repcut.db
```

**Amend Prompt 02, Deliverable 1** — six tables, not three:

| Table | Holds | Key |
|---|---|---|
| `projects` | as the guide | `id` |
| `media_blobs` | one row per distinct byte sequence, and every property *of the bytes*: `sha256`, size, path relative to `$DATA_DIR`, duration, display width/height, rotation, `fps_source`, `fps_normalized`, video codec, audio sample rate | `sha256` |
| `media_files` | one row per *reference*: `project_id`, `sha256`, display name, added_at, position | `id`, unique `(project_id, sha256)` |
| `derived_artifacts` | proxy, thumbnail strip, and every later derived render | unique `(sha256, artifact_kind, params_version)` |
| `upload_sessions` | in-flight transfer state — see 7 below | `id` |
| `jobs` | as the guide | `id` |

The guide's `media_files` name is kept for the table later prompts join on. The
byte-describing columns move to `media_blobs` because they are properties of
the bytes, not of the reference; two references to one clip cannot then
disagree about its frame rate. The user's original filename is a **display name
column**, never a path component — per `secrets.md`, no stored path derives from
a user-supplied name.

**Derived artifacts are content-addressed too**, keyed `(sha256,
artifact_kind, params_version)`. Without this, dedup saves the source and
re-encodes the proxy once per project, and there is no invalidation path when a
later prompt changes proxy settings. `params_version` is an integer per
`artifact_kind`, declared in one module and bumped in the same commit as any
change to that artifact's encode recipe. **A bump never mutates or deletes an
existing file** — it changes the key, so superseded artifacts become
unreferenced rather than wrong.

**Refcounting and orphan collection are required, and deferred.** Prompt 02
ships no delete endpoint — the guide gives it none — so nothing can be orphaned
during Prompt 02. The obligation begins with the first delete surface. Prompt
02 adds **no stored refcount column**: the reference count is derivable
(`SELECT COUNT(*) FROM media_files WHERE sha256 = ?`), and at single-user scale
a derived count that cannot drift beats a stored one that can. See Consequences
for the owning prompt.

### 7 — `upload_sessions`

**Add to Prompt 02, Deliverable 1:** `upload_sessions` — `id`, `project_id`,
display name, declared size, chunk size, `bytes_received`, `declared_sha256`,
`.part` path relative to `$DATA_DIR`, status, `created_at`, `updated_at`.
Durable offset state is what makes success criterion 4 idempotent rather than
best-effort.

Plus a **partial unique index** `uq_upload_sessions_in_progress` on
`(project_id, declared_sha256) WHERE status = 'in_progress'`. Durable state
without a lookup path only resumes for a caller that still holds the session id,
and criterion 4's caller is a test client that keeps the id in memory across the
kill — so the criterion passes while a browser tab refreshed mid-upload does
not. That tab retries as a new session and abandons the first `.part` with
nothing referencing it. The index is both the lookup key
(`WHERE project_id = ? AND declared_sha256 = ? AND status = 'in_progress'`) and
the constraint that makes the orphan unconstructible. It is partial in both
directions on purpose: scoping it to `in_progress` keeps re-uploading a
completed clip legal, and SQLite's NULL-distinctness keeps sessions with no
declared hash out of it, since nothing identifies them.

**Add to Prompt 02, Deliverable 5 (Track B):** the upload UI **looks up
in-progress sessions on mount** rather than assuming it holds the id it started
with. A refreshed tab, a crashed tab and a reopened browser all arrive with no
id; the index makes the naive retry fail loudly instead of orphaning a
part-file, and the UI has to treat that as the resume signal.

**Add to Prompt 02, Constraints:**

> - The authoritative resume offset is `min(bytes_received recorded in the DB,
>   actual size of the `.part` file on disk)`. Either can be ahead of the other
>   depending on where the kill landed — a flushed write with no commit leaves
>   the file ahead, a commit whose write was lost leaves the DB ahead — so the
>   minimum is the only value safe in both directions. The client re-sends at
>   most one chunk.
> - Finalize hashes the assembled file, compares against the client-declared
>   hash when one was supplied, rejects a mismatch with a named error, then
>   moves the file atomically into the content-addressed store. A blob that
>   already exists is *not* rewritten; the session ends by inserting the
>   `media_files` reference only.
> - Re-running a completed upload creates one blob and one `media_files` row,
>   whatever the number of interruptions.

## Consequences

- **`make verify-02` cannot pass unsigned.** Criterion 16 exits 1 while any box
  in `docs/manual-checks/prompt-02.md` is unticked. That is deliberate: the
  automated criteria prove the code handles synthetic VFR and rotation, and only
  a human with a phone can prove it handles the real thing. The gate is honest
  about which is which instead of dropping the criterion.
- **A project folder is no longer self-contained or movable.** This is the cost
  being accepted. Copying `$DATA_DIR/projects/{id}/` to another machine yields
  a project whose every clip is missing, because the bytes live in
  `$DATA_DIR/media/` and only the DB maps one to the other. The unit of backup,
  move or restore is `$DATA_DIR` as a whole — projects folder, media folder and
  `repcut.db` together, or nothing. Prompt 13's migration tooling inherits this;
  no prompt before it may assume a portable project directory.
- **Refcounting and orphan GC are owed to Prompt 12.** It is the prompt the
  guide already designates for v1's deferred operational concerns, and a GC
  needs exactly what it builds: a fixture corpus and a gate that fails the
  build — "zero unreferenced blobs and zero unreferenced derived artifacts
  after a project delete". **Earlier
  trigger:** if any prompt before 12 ships a delete or remove surface — a
  "remove clip" action in the library, a project delete, an export cleanup —
  that prompt inherits refcounting and GC and this deferral ends there.
  Recorded in `docs/reports/prompt-02.md` under OPEN ISSUES.
- **`$DATA_DIR` must not sit inside a cloud-sync folder.** This repository lives
  under a OneDrive-synced path, and `DATA_DIR=./data` anchors on the repo root,
  so the default puts every byte of gym footage inside the sync scope. Three
  concrete failures: Files-On-Demand leaves placeholder stubs that `ffprobe`
  cannot read (gate criteria 6, 7, 8); the sync agent contends for a lock on
  exactly the `.part` file criterion 4 kills mid-write; and criterion 13 pushes
  2GB into cloud quota on every slow run. Landing in this amendment as code:
  `detect_sync_root()` in `engine/repcut/config.py` returns the *provider label
  only* — never the path, per `secrets.md` — and the engine logs a structlog
  `data_dir_under_cloud_sync` warning at startup; `scripts/check_env.py` gains a
  matching row, `hard=False`. WARN, not FAIL: a fresh clone must still pass its
  environment check, and CI's `DATA_DIR` is never synced. `.env.example` gains a
  comment naming the requirement; the real value stays in the untracked `.env`.
- **A second in-flight upload of the same clip into the same project is now a
  database error, not a silent second `.part`.** The endpoint has to catch that
  integrity error and treat it as "resume the existing session", and Track B's
  UI has to look the session up on mount instead of relying on an id it may have
  lost. Both are obligations of the deliverables that build them, recorded in
  `docs/reports/prompt-02.md`.
- **Analysis and storage now share one identity.** `gemini-usage.md`'s cache key
  `(video_hash, scene_id, prompt_version)` and the derived-artifact key
  `(sha256, artifact_kind, params_version)` are the same shape. Prompt 03
  onwards keys everything on the blob hash, so re-adding a clip to a second
  project reuses its analysis, its proxy and its Gemini cache — a cache miss on
  a repeat run stays the bug that rule says it is.
- **No already-passed gate is invalidated.** `scripts/verify_01.sh` is criterion
  14 of `verify_02.sh`, so the guarantee is enforced rather than promised. The
  sync-root row added to `check_env.py` is a WARN and criterion 9 of
  `verify_01.sh` accepts additional rows and a WARN-heavy exit.
- **Prompt 03 onwards inherits six tables instead of three.** Any later
  amendment that adds a media property adds it to `media_blobs` if it describes
  the bytes and to `media_files` if it describes one project's use of them. That
  is the test.

## Principle check

**P4 (privacy & honesty) — strengthened, three ways.** Content addressing keeps
the user's filename out of the filesystem entirely; it survives as a display
name column, so no stored path derives from user input. The sync-root warning
addresses the case where the operating system silently uploads gym footage to a
third party — "footage stays local" is P4's core claim, and a `$DATA_DIR` inside
OneDrive violates it without anyone choosing to. And the warning itself is
scrubbed: it logs `provider="onedrive"`, never the path, because the path
contains the OS username.

**P5 (€0) — protected.** No paid service, no account, no dependency outside the
approved stack. A synced `$DATA_DIR` also consumes paid cloud storage quota —
2GB per slow-test run — so the warning defends the budget as well as the
privacy claim.

**P1 (natural only) — untouched.** Proxies and CFR normalization re-encode
frames the camera captured. Nothing is generated, inserted or replaced.

**P2, P3 — untouched.** Prompt 02 makes no AI recommendation and logs no taste
event; the first override surface is Prompt 04.

**Licensing (`frontend-and-licensing.md`)** — the new engine dependencies
(`sqlalchemy[asyncio]`, `aiosqlite`, `alembic`, `python-multipart`, dev-only
`psutil`) must each have their licence verified as AGPL-compatible and recorded
in `docs/reports/prompt-02.md`. This amendment does not pre-approve them.
