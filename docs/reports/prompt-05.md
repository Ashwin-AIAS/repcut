# Prompt 05 — timeline assembly: music, usable footage, beat-synced cuts
PUSH PENDING: run `git push -u origin feat/timeline-assembly` from Git Bash
Branch: feat/timeline-assembly (stacked on prompt-04 @ e5c2bb0) · Built 2026-10-12 in one scheduled run · Human checks and real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** Segment quality and
energy are measured on proxies normalised with it.

## Plan (pre-approved under amendment 016, written 2026-10-12)
Engine, `engine/repcut/`:
- `music/analysis.py` — decoded mono PCM in, `TrackAnalysis` out (librosa):
  BPM, beat times (the returned times, never `tempo × n`), downbeats (bar phase
  by onset strength), per-bar energy, sections (intro / build / drop / outro)
  and a tempo-stability verdict; an unstable grid is rejected with a reason.
- `music/library.py` — scans `$DATA_DIR/music/` (amendment 004: the music
  library lives in `$DATA_DIR`, not the repo's `data/`). A source is accepted
  only as a regular file directly in that folder, resolved inside it, with an
  audio extension; it is decoded only through `ffmpeg_builder`'s validated
  source; every derived path is keyed by the track's sha256, never its name.
  Analysis once per (sha256, analysis version), table `music_tracks`.
  Writes the library's README (royalty-free or owned tracks only) if absent.
- `music/recommend.py` — pure: rank analysed tracks against the project's
  duration-weighted energy, the scenes' energy tags and the theme's pace; top 5
  with a reason sentence each.
- `edit/signals.py` — per clip, from the proxy: audio RMS, motion and shake
  (global-translation jitter) in 0.5 s windows, plus speech regions from
  silero-vad. Cached as JSON under the clip's derived path, versioned.
- `edit/vad.py` — silero-vad v5.1.2 ONNX through onnxruntime (MIT, CPU). Not the
  `silero-vad` pip package: it requires torch, which amendment 003 defers to
  Prompt 07. Model fetched once by `scripts/fetch_models.py` from the pinned
  release, sha256-checked, stored under `$DATA_DIR/models/`.
- `edit/segments.py` — pure: signals + scene boundaries (+ boundary overrides,
  owed by amendment 012 row 11) -> usable segments with quality and energy.
- `edit/planner.py` — pure: segments + beat grid + target + theme pace + seed +
  user decisions -> plan. Every unlocked cut on a beat; drop aligned with the
  highest-energy footage; min shot length; deterministic with seed. No grid ->
  pacing grid, `snapped=false`.
- `edit/resync.py` — decisions (track, no-music, target, seed, locks, removed,
  forced, boundary overrides) applied by re-planning everything not locked;
  locked shots are copied, never recomputed.
- `edit/ducking.py` — speech regions -> gain envelope (attack 150 ms, release
  400 ms, duck under speech, swell on high-energy action) -> one FFmpeg
  `volume` expression; the music stem rendered and measured.
- Migration 0004: `music_tracks`, `edit_plans`; `taste_events` gains the edit
  override kinds and a nullable `pack_id`. Every override -> one taste event.
- API + jobs: music scan/analysis job, signals job, edit get/override/regenerate,
  recommendations. UI: edit review screen (beat-marked cut timeline, music
  picker with AI top 5 + library + "export without music", per-cut remove/lock,
  regenerate).
- Gate: `scripts/verify_05.sh` (+ `--auto`), checks in `verify_05_checks.py`.
- STOP artifact (deferred): `scripts/edit_review.py` renders the 30 s Moody edit
  from real footage with a generated track (never downloaded music).

### Assumed
- Synthetic, generated tracks only (fixtures, gate, review artifact).
- Bar = 4 beats. Calm sections cut every 2 bars, peaks every bar or half bar,
  scaled by the theme's pace; min shot 0.6 s and never under one beat.
- "Rendered audio" for the ducking criterion is the ducked music stem; the full
  voice + music mix is the export's job (Prompt 06).

## Built (2026-10-12)
Commits: c19c7b9 (analysis, planner, re-sync), 13e261c (signals, segments,
speech, ducking, builders), 54edcd5 (library, recommend, `[pace]`, migration
0004, service, jobs, API), 87db0aa (UI), 06b129e (gate, review script, checklist).
- Music: beats tracked on low-band onsets with a half-beat phase check; tempo
  from a fit over all beats; drifting tempo rejected with a reason. Beats land
  ~20 ms (max 34 ms) from generated truth at 75-160 BPM.
- Footage: motion, shake (camera translation from LK corners + RANSAC, high
  frequency only), loudness and speech per 0.5 s window, cached per clip.
- Planner and re-sync as planned; recommendations with a reason sentence.
- API: `GET /projects/{id}/edit`, `POST .../edit/overrides`, `POST .../edit/signals`,
  `GET /music/tracks`, `POST /music/scan`; jobs `music_scan`, `edit_signals`.
- UI: edit panel (AI pick + reason first, whole library, unusable tracks with
  why, export-without-music checkbox, beat/bar markers, lock/remove per cut,
  layout transition on re-sync, music volume curve).

## Decisions made autonomously
- Full-band beat tracking locked onto off-beat hi-hats (0.27 s phase error);
  low-band tracking plus the phase check fixed it. Onset hop 256 (11.6 ms).
- Phase correlation read moving content as shake (0.04 on a steady pattern =
  shaken); LK + RANSAC separates them (steady 0.0005, pan 0.001, shaken
  0.03-0.05). Shake must last 1 s: a hard cut is one window of apparent jitter.
- The drop's opening slot is filled first and asks for top energy; energy is
  matched as a rank, so absolute session loudness does not matter.
- "Remove a cut" removes the shot's source range (amendment 018 #3).
- Duck 12 dB under speech, base -3 dB, swell +3 dB on top-energy shots without
  speech; attack 150 ms, release 400 ms. One flat FFmpeg expression with a
  token allow-list.
- The review script applies Moody for its render only, so no grading choice
  and no taste event is invented.

## Assumed
- Dead space: motion < 1.2 codes and no speech for 1 s. Bars of 4 beats.
  Session energy maps to a target tempo of 80-150 BPM.
- One edit per project (`edit_plans` keyed by project).

## Deviations
Amendment 018 (PROPOSED): where music and the speech model live, what remove
removes, which audio the ducking criterion measures, a generated track for
the review, `[pace]` in theme files.

## Real footage (local, not committed)
Three local test clips (one with speech), ingested into a scratch library:
`edit_review.py` made a 29.7 s Moody edit, 7 shots on a generated 101 BPM
track, in 44 s. Measured music under speech vs the same window undocked:
12.0, 12.0, 12.0, 11.7 dB. Speech model: no false hits on generated music,
noise or tone; 10 regions on the clip where someone talks. Two of the phone
.MOV clips in the footage folder are truncated ("moov atom not found"); the
third now opens since the move off OneDrive.

## Gate (verify-05-auto), 2026-10-12, Linux build shell, FFmpeg 8.1.3
**Not run as a whole** (one shell call is capped at 180 s, and criterion 12
nests the 27-minute verify-04). Criteria run individually, each seen to finish:
1 PASS (grid 0.0 ms, truth 30.7 ms, duration within 0.3 %, min 1.83 s,
deterministic) · 2 PASS (locked 2/2 byte-identical, unlocked 7/7 on the new
grid) · 3 PASS (cut 0 ms from the drop, energy percentile 0.96) · 4 PASS (300
segments, worst re-plan 5.7 ms) · 5 PASS (12.0 dB undocked, 9.0-11.2 dB vs
base) · 6 PASS · 7 PASS · 8 PASS (32 tests) · 9 PASS (8 tests, 0 static
offenders) · 10 PASS · 13 PASS.
**Not run: 11** (needs Chrome and `make dev`; this shell has neither) **and 12
as a whole**. Of verify-04's criteria, the ones this prompt could affect were
re-run and pass: 2, 3, 4, 9c, 9d, 11, 12, 13, 15. 14 [HUMAN] PENDING.
Mutations, each caught: the [HUMAN] line deleted from verify_05.sh (2 tests
fail); `librosa.load(` planted in edit/ (criterion 9 fails).
Engine suite per file: 38 files, all pass (skips as before: 2 GB upload,
Windows-only loop tests). UI from a Linux mirror: tsc, eslint, vitest 219 pass.
Hooks run by hand before every commit (Windows-only pre-commit shim, so
`--no-verify` after the guards); one commit was scanned only as part of the
combined staged set, so gitleaks was re-run over every branch commit: clean.
`make test-gpu`: not applicable (no GPU code).

## Built on unverified
- STOP A (operator hable PROVISIONAL): every proxy measured for motion, shake
  and energy is normalised with it.
- STOP B: the Moody look of the review edit; the themes' `[pace]` values are
  the agent's, unjudged.
- verify-04 criteria 6, 7, 16 and this gate's 11 have never run on this code.
- The speech model is checked for false positives only; real-speech accuracy
  is a manual box.

## Open questions for the human
- Amendment 018: accept as written?
- Pace values per theme (Moody: 2-bar shots even in the drop) are taste.

### [HUMAN] criteria
14 (every box in prompt-05.md) PENDING.

BUILD COMPLETE (verification pending)
