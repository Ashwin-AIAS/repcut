# Prompt 03 — Analysis Engine

**Status: interim checkpoint.** Track A (engine) is complete and gated at the
module level; Track B (UI) and the final gate-loop pass have not started yet.
This report will be refreshed at `/gate 03`. Recorded now because
`docs/prompts/run-prompt-03.md`'s own sequencing calls for a checkpoint here,
and it's a coherent place for the session to resume from if it ends.

## Built

Two amendments landed before any implementation, per the kickoff doc's
Deliverable 0:

- **007** — the Next.js 14→16 upgrade, approved and shipped at the Prompt 02
  gate, never written up. Paper-only; no code change.
- **008** — Prompt 03's six collisions between the guide's text and this repo
  (package path, frame storage, frame source, boundary timebase, fixtures,
  detection input), each resolved with reasoning tied to the rules and
  amendments they collide with.

One process fix, separate from the amendments: `.claude/commands/run-prompt.md`
step 2 told the agent to read the build guide's path "from `.env`", which
`.claude/settings.json` denies by design — unreachable by construction, and it
would have blocked every later `/run-prompt`. Rewritten to glob the repo root
for `*Prompt_Guide*`, falling back to an already-exported `REPCUT_GUIDE_PATH`
only. Ninth instance of the "guard reads as covering something it doesn't"
pattern catalogued in `docs/guide-amendments/006-…`'s table.

Two debt items folded in, per the kickoff doc:

- **Open issue 5** — `scripts/` is now under `ruff` (`S` ruleset included), via
  a root-level `pyproject.toml` (its own `[tool.ruff]`, since `engine/`'s is
  also that package's build config). 32 real findings fixed — two genuine
  `ASYNC` correctness issues in `cdp_browser.py`, three dead `# noqa: S603`
  removed now that a scanner reads the file, the rest line-length/style/
  justified-noqa. `security.md` corrected.
- **Open issue 7** — `make dev` interrupted with Ctrl-C now returns 130
  instead of a raw traceback (`scripts/posix_shell.py` catches
  `KeyboardInterrupt` around the child process call).

Engine (Track A), in dependency order:

- **Schema** (`db/models.py`, migration `0002`): `Scene` (one row per detected
  scene, keyed `(sha256, detector_params_version, sequence_index)`) and
  `GeminiSceneCache` (keyed `(scene_id, gemini_prompt_version)`).
- **`analysis/params.py`, `types.py`** — recipe/version constants
  (`SCENE_PARAMS_VERSION`, `FRAME_PARAMS_VERSION`), shared dataclasses
  (`SceneBoundary`, `EnergyMeasurement`).
- **`analysis/scenes.py`** — `detect_scenes`, PySceneDetect's `ContentDetector`
  against the proxy.
- **`media/ffmpeg_builder.py`** — `build_frame_extraction`, reads the source,
  conditionally HDR-tone-maps (`zscale`+`tonemap` chain, probed once per clip),
  strips metadata (`-map_metadata -1`). `media/metadata.py` gained
  `parse_color_properties`.
- **`analysis/sampler.py`** — `pick_frame`, sharpest of 3 candidates by
  Laplacian variance.
- **`analysis/motion.py`** — `compute_scene_energy`, optical flow (Farneback)
  + audio RMS (`astats`) against the proxy.
- **`analysis/gemini_client.py`, `cache.py`** — httpx call to Gemini 2.0
  Flash's REST endpoint, cache-first lookup, token-bucket rate limiter
  (RPM in-memory, daily counter persisted to `$DATA_DIR`), capped
  exponential backoff, one retry on malformed JSON then `None`.
- **`analysis/pipeline.py`** — `run_analysis`, the `JobType.ANALYSIS` handler,
  five resumable/idempotent stages, auto-enqueued right after a successful
  ingest (`api/uploads.py`).
- **New routes**: `GET /media/{sha256}/scenes`, `GET
  /media/{sha256}/scenes/{scene_id}/frame` (Range-aware).

Gate scaffold (`gate-runner`, first pass): `scripts/verify_03.sh` +
`verify_03_checks.py` (19 criteria), `docs/manual-checks/prompt-03.md`
(unticked), new `conftest.py` fixtures (HDR-tagged clip, a
motion/loudness-step clip).

443 engine tests passing (up from 291 at the Prompt 02 merge). `ruff`,
`ruff format`, `mypy --strict`-equivalent config all clean throughout.

## Decisions made autonomously

- **A sampled frame is a column on `Scene`, not a `derived_artifacts` row.**
  That table's unique key is 1-row-per-`(sha256, kind, version)`; a clip has
  N scenes. Widening the key would have touched Prompt 02's already-gated
  `ingest.py`. Full reasoning in amendment 008.
- **Scene boundaries are seconds-against-source plus a source frame index**,
  never a bare frame number — the two files per clip (source, proxy) have
  different timebases and one is VFR.
- **Detection reads the proxy** (a timing decision, CFR already solved);
  **sampling reads the source, always** (amendment 008's central resolution,
  and the reason this prompt exists — `docs/future-prompts/prompt-03-frame-source.md`).
- **HDR tone-mapping is conditional**, not unconditional: the source's actual
  `color_primaries`/`color_transfer` are probed once per clip (extending the
  existing `build_probe` call, no second ffprobe invocation) and the filter
  graph branches — an unconditional transform would risk altering
  already-correct SDR footage and cost compute on the common case.
- **The Gemini cache key folds `video_hash` into `scene_id`'s FK chain**
  rather than storing it as a literal column — a `Scene` row is already
  unique per `(sha256, detector_params_version, sequence_index)`, so the
  three-part cache key from `gemini-usage.md` is preserved, just not spelled
  out as three literal columns. The skill file's example was corrected to
  match.
- **Gemini's daily rate-limit counter persists to a JSON file in
  `$DATA_DIR`, not a new table.** It counts *attempts* (including ones that
  never produced a cacheable answer), a different concern from
  `gemini_scene_cache`'s cached *answers*.
- **A `gemini_scene_cache` row is written only after a real API round-trip**
  (parsed success, or malformed-after-retry) — never after a rate-limiter
  refusal or an exhausted backoff. This is what keeps "repeat run costs zero
  calls" (cache hit) and "offline completes and the next run tries again"
  (no cache entry) both true without contradiction.
- **Analysis auto-enqueues immediately after a successful ingest** — matches
  the guide's own "upload → AI analyzes" core loop. Flagged as an assumed
  default at plan time; no objection raised.
- **PySceneDetect (BSD-3-Clause) + `opencv-python` (Apache-2.0, not
  `-headless`)** cover scene detection, sharpness scoring, and optical flow —
  one CV dependency for three needs, since `scenedetect` already requires
  `opencv-python` and a second install would collide on the same `cv2`
  namespace. No torch (amendment 003 stands).

## Deviations from the guide

Amendment 007 (Next.js version line, paper-only) and amendment 008 (Prompt
03's six conflicts — package path, frame storage, frame source, boundary
timebase, fixtures, detection input) — see
`docs/guide-amendments/007-nextjs-14-to-16.md` and
`docs/guide-amendments/008-prompt-03-frame-source-and-storage.md`.

## Open questions for the human

- **Auto-enqueue after ingest** (above) — proceeding on it as decided; flag if
  you'd rather analysis be a manual trigger.
- **Gate criterion 16 (Ctrl-C → exit 130)** could not be fully exercised in
  `gate-runner`'s sandboxed shell — `GetConsoleWindow() == 0` there, so there's
  no real console to deliver `CTRL_C_EVENT` from. It currently SKIPs cleanly
  with that reason. I'll try to validate it from an interactive terminal
  during the gate loop; if I can't, it stays open for you to check once with
  a real `make dev` + Ctrl-C.

## Gate status

Not yet run end-to-end — `verify_03_checks.py` was written against an assumed
API before Track A's real function signatures existed (`run_analysis` takes a
`JobContext`, not the kwargs the first draft assumed; `sampler.py` exports
`pick_frame`, not `sample_scene_frame`). Reconciling the gate against the real
code, building Track B, and running the full gate loop are next. This section
will carry full PASS/FAIL/measured-value results at `/gate 03`.

## Risks / known gaps

- `verify_03_checks.py` needs a reconciliation pass against the shipped
  function signatures before any of criteria 2–14 can run for real.
- Track B (UI: scene strip, energy sparkline, P4 disclosure) does not exist
  yet — criteria 17 and 19 depend on it.
- Criterion 16 needs a real-terminal check (above).
- `make test-gpu` not applicable — nothing in this prompt touches GPU code
  (amendment 003: no torch until Prompt 07).
