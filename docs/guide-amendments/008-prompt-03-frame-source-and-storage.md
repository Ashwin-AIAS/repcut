# Amendment 008 — Prompt 03: package path, frame storage, frame source, boundary timebase, fixtures, detection input
Date: 2026-08-28
Affects: Prompt 03 (Deliverables 1–5, Constraints, Success Criteria), Section 4
(Architecture — Storage, extended)
Status: ACCEPTED

## What the guide says

Prompt 03, Deliverables 1–3, abridged to the clauses this amendment touches:

> 1. `engine/analysis/scenes.py` — PySceneDetect (ContentDetector) per media
>    file → scene list (start/end frames); store in new `scenes` table
> 2. `engine/analysis/sampler.py` — 1 representative frame per scene (sharpest
>    of 3 candidates via Laplacian variance), saved to
>    `data/projects/{id}/analysis/frames/`
> 3. `engine/analysis/gemini_client.py` — Gemini 2.0 Flash client: …

Prompt 03, Constraints:

> - Analysis of a 10-min session (≈15 clips) must finish < 5 min on the ROG
>   (budget: scene detect is the bottleneck; downscale to 480p for detection)

Prompt 03, Success Criteria:

> - Run on 3 real gym clips: scenes detected, ≥90% of scenes get valid VLM
>   JSON, energy curves non-flat

The guide does not say which of a clip's available files a sampled frame is
read from — Deliverable 2 names only the sampling method (sharpest of three
candidates), not the source file.

## What we found

Six collisions between this text and the repo as Prompt 02 left it.

| # | Collision | Resolution |
|---|---|---|
| 1 | The guide's package path, against the installed package at `engine/repcut/` | `engine/repcut/analysis/` |
| 2 | The guide's per-project frames directory, against amendment 004 §6's content-addressed store | A sampled frame is a `DerivedArtifact`, keyed `(sha256, artifact_kind, params_version)`, not a project-folder file |
| 3 | Which of the two files per clip (source, or the 720p CFR proxy) a frame is sampled from — the guide is silent | The source, always. Asserted by dimension, not by path |
| 4 | The guide stores scene boundaries as frame numbers; there are two files per clip with two different timebases, and the source is VFR | Boundaries stored as seconds against the source, plus the source frame index |
| 5 | The guide's success criterion needs real gym clips; `testing.md` and `git-and-ci.md` forbid committing media | The same split amendment 004 §1 made: synthetic fixtures in `verify-03`, plus a human-signed `docs/manual-checks/prompt-03.md` |
| 6 | The guide's runtime budget names a detection *resolution* (480p) but not a detection *input file* | Detection may read the proxy — a timing decision, and the proxy is CFR. Sampling may not |

Collision 2 is the one that costs most to retrofit, because it is a schema
key. Collision 3 is the one this prompt exists to get right —
`docs/future-prompts/prompt-03-frame-source.md`, written during Prompt 02
while the measurement was fresh, names it in detail and is not repeated here.

## Why the guide's version doesn't work

**1 — The package path in the guide does not exist.** There is no bare
`engine/` Python package; Prompt 01 created `engine/repcut/`, and amendment
004 §2 already resolved this exact collision for `ffmpeg_builder.py` the same
way. Picking the guide's literal path would put every module in this prompt
outside the installed package, unimportable as written.

**2 — "Per-project frames directory" and "content-addressed store" cannot both
be true, and "one row per key" cannot hold once the key repeats per scene.**
Amendment 004 §6 replaced `data/projects/{id}/source/` because a derived
artifact is a pure function of *(source bytes, encode recipe)*, and storing it
under a project folder breaks the moment two projects reference the same clip,
or a later prompt changes the recipe with no invalidation path. A sampled
frame is exactly this shape of thing: a pure function of *(source bytes,
extraction recipe — tone-map target, candidate count)*. Giving it a
project-folder path re-opens the same failure amendment 004 closed.

But `derived_artifacts` is more than content-addressed — it is
*singular*-per-key: `uq_derived_artifacts_key` is `(sha256, artifact_kind,
params_version)` with exactly one row assumed, because a clip has exactly one
proxy and one thumbnail strip. A clip has **N** sampled frames, one per
detected scene. Reusing that table for frame storage either breaks its own
uniqueness (N rows sharing one key) or forces a fourth, per-scene column into
the constraint — and `ingest.py`'s `_existing_artifact` / `_record_artifact`
already read and write that table assuming the three-column key resolves to
at most one row. Widening it is a change to code Prompt 02 already gated, for
a table this prompt does not need to touch at all.

**3 — Nothing errors, which is why it needs an amendment and not just care.**
The proxy is 406×720 for portrait phone source (measured on real clips,
recorded in `docs/reports/prompt-02.md` under *Open issues* and in
`docs/future-prompts/prompt-03-frame-source.md`). A 406×720 JPEG is a valid
image; Gemini accepts it, returns well-formed JSON, and the Pydantic schema
validates it. The failure is a silent quality regression with no exception, no
malformed response, and no log line that looks wrong — `ffmpeg.md` already
states "proxies are preview-only" as a principle, but a principle is not a
gate, and this is the specific place the principle gets tested by convenience.

**4 — A frame number with no timebase is the desync bug in a new costume.**
`ffmpeg.md`'s VFR warning is explicit: "Beat syncing, cut timing, and
interpolation all silently drift on VFR input. … Never assume the container's
nominal fps." The source is VFR (amendment 004's real-footage findings); the
proxy is CFR by construction. A bare frame number does not say which file's
frame-count it counts against, and the two disagree for the same wall-clock
span. Storing seconds against the source removes the ambiguity at the type
level rather than by convention.

**5 — Real footage cannot be a gate input**, for the same reason amendment 004
§1 gave for Prompt 02: `testing.md` forbids committing media outright, and the
repository is public, so a criterion needing real clips cannot be `make
verify-03`'s input — but the criterion is testing something synthetic fixtures
cannot reach (a real Gemini response to a real gym scene, a real reviewer's
eye on whether a boundary lands where the cut is).

**6 — Detection and sampling are different questions the guide's one budget
line conflates.** "Downscale to 480p for detection" names an input resolution
but not which *file* supplies it. Detection needs shot boundaries in time —
the proxy, already CFR, already local, is the right and cheap answer.
Sampling needs the frame Gemini will actually see, which collision 3 already
settled must be the source. Treating both as "the input" would either slow
detection to source-resolution for no benefit, or — worse — reintroduce
collision 3 by making "the file analysis reads" one ambiguous thing instead of
two deliberate ones.

## Proposed change

**1 — Package path.** Amend every `engine/analysis/…` path in Prompt 03's
Deliverables to `engine/repcut/analysis/…`.

**2 — Frame storage.** Amend Deliverable 2: drop `saved to
data/projects/{id}/analysis/frames/`. The new `scenes` table (resolution 4)
already carries one row per scene — that row is the discriminator a sampled
frame needs and `derived_artifacts` cannot supply, so the frame is a column on
it: `sampled_frame_path` (nullable until the sampler runs), populated with a
path built the same way every other derived file is — through
`media/store.py`'s existing directory helpers, under
`$DATA_DIR/media/derived/<sha[:2]>/<sha>/sampled_frame/<params_version>/scene_<sequence_index>.jpg`
— without adding a row to `derived_artifacts` or touching its unique key.
`engine/repcut/analysis/params.py` declares `FRAME_PARAMS_VERSION` beside a
frozen recipe (tone-map target, candidate count), in the same style as
`media/artifacts.py`'s `PARAMS_VERSION` table and for the same reason:
changing the recipe means bumping the version in the same commit, and a bump
does not delete the superseded frame, it just stops pointing at it.

**3 — Frame source.** Add to Prompt 03, Constraints: **frame extraction reads
the source file, never the proxy, unconditionally.** The gate asserts this by
measuring the sampled frame's own dimensions against
`media_blobs.display_width` / `display_height` — never by asserting the path
it was read from, which a later resize would make pass for the wrong reason.
Extraction owns its own tone-map (the source is HDR: HEVC Main 10, BT.2020,
HLG) and strips metadata before the frame is written — both riders of the
same "read the real file" fix, detailed in
`docs/future-prompts/prompt-03-frame-source.md`.

**4 — Boundary timebase.** Amend Deliverable 1: the `scenes` table stores
`start_seconds` / `end_seconds` (float, against the source's timebase) as the
authoritative boundary, plus `start_frame_source` / `end_frame_source` (the
source's own frame index, derived from the seconds value) for callers that
need a frame handle. No column stores a proxy-relative frame number.

**5 — Fixtures.** Amend Prompt 03, Success Criteria — replace:

> ~~Run on 3 real gym clips: scenes detected, ≥90% of scenes get valid VLM
> JSON, energy curves non-flat~~

with the automated/`[HUMAN]` split amendment 004 §1 established: synthetic
fixtures (including a VFR clip and an HDR-tagged clip) drive the automated
gate criteria; `docs/manual-checks/prompt-03.md` — no unticked boxes — is a
separate criterion the gate enforces and never passes on its own.

**6 — Detection input.** Add to Prompt 03, Constraints: scene *detection* may
read the proxy (a timing decision, and the proxy is CFR — no VFR handling to
re-derive in the detector). Frame *sampling* may never read the proxy —
resolution 3 already settled that. Resolution 4's seconds-against-source
boundary is what keeps the two able to agree on where a scene is without
sharing a frame-number space.

## Consequences

- `engine/repcut/analysis/` is the package every downstream prompt imports
  from — consistent with amendment 004 §2's resolution for `ffmpeg_builder`.
- Sampled frames are content-addressed on disk the same way every derived
  artifact is, and their DB reference sits on the row that already
  disambiguates them per scene rather than widening `derived_artifacts`'
  three-column key to a fourth. Amendment 004 noted that analysis and storage
  "share one identity" once Prompt 03 lands; this is that landing — `(sha256,
  …)` is still the root of every path and every cache key. Re-adding a clip to
  a second project reuses its sampled frames, exactly as it already reuses its
  proxy, because both hang off `sha256` and neither hangs off `project_id`.
- Scene boundaries carry two numbers, not one, and any later prompt reading
  them must not assume a bare frame count is meaningful without knowing which
  file's timebase produced it — this amendment removes that ambiguity rather
  than documenting a convention to remember.
- `docs/manual-checks/prompt-03.md` is a new deliverable, structured like
  `docs/manual-checks/prompt-02.md`; `make verify-03` cannot pass unsigned.
- Detection and sampling read two different files for one scene list. That
  asymmetry is real and is recorded here so a later prompt does not
  "simplify" it back into one file and quietly reintroduce collision 3.
- No already-passed gate is invalidated. This amendment adds columns and a
  new artifact kind; it changes nothing Prompt 02 shipped.

## Principle check

**P4 (privacy & honesty)** — this amendment is what makes Prompt 03's P4
boundary actually hold, not just count correctly. "One sampled frame per
scene" would stay true by count even sampling from the proxy; resolution 3 is
what keeps the frame Gemini sees the one the camera actually captured at
usable detail, and the tone-map and metadata-strip riders it carries close the
two related defects the same measurement found.

**P1 (natural only)** — untouched. Extraction reads frames the camera
captured; nothing is generated, replaced or inserted.

**P2, P3** — untouched. Prompt 03 makes no user-facing AI *decision* yet (that
starts at Prompt 04); it produces the data those decisions will read.

**P5 (€0)** — untouched. No new paid service; the new dependencies (scene
detection, optical flow) are chosen free and AGPL-compatible per Prompt 03's
own deliverable list, licence recorded in the session report.

**`testing.md` / `git-and-ci.md`** — resolution 5 is required by both; no
media is committed as a result of this amendment.
