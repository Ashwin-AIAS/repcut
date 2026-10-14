# Prompt 06 — captions and the export (end of Wave 1)
PUSH PENDING: run `git push -u origin feat/render-out` from Git Bash
Branch: feat/render-out (stacked on feat/timeline-assembly @ 07b6b1a) · Built 2026-10-13 in one scheduled run · Human checks and real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** The export
normalises every HDR source with it, exactly as the proxy does.

## Plan (pre-approved under amendment 016, written 2026-10-13)
- `captions/`: `types`, `transcribe` (faster-whisper small, local, multilingual,
  word timings, the music guard), `timeline` (words onto the edit by midpoint,
  ids that survive a re-cut), `styles` + three TOML styles, `ass` (escaped
  subtitle writer), `service` + `jobs` (transcribe per clip, edits, taste).
- `export/`: `presets.toml` (Reel, Square, Landscape; encode settings),
  `reframe` (centre crop + per-scene offset), `render` (one segment per shot
  from the original file, digest-named, frame-exact joins, music stem, mux,
  check), `check` (the platform check on the file), `service` + `jobs`.
- Builders `build_export_segment` / `build_export_mux`; migration 0005
  (`caption_settings`, `export_settings`, new taste kinds); routes
  `/projects/{id}/captions[/transcribe]`, `/projects/{id}/export[/settings]`,
  `/projects/{id}/export/{preset}/file`; UI `CaptionPanel`, `ExportPanel`.
- Gate `verify_06.sh` (+ `--auto`), `verify_06_checks.py`, `e2e_export.py`,
  `speech_fixture.py`; review `wave1_review.py`; checklist `prompt-06.md`.

## Built (2026-10-13)
Commits: 6a70d53 (captions), 15c2d2f (export render), 07fa309 (settings, jobs,
routes, 0005), cf2d8c2 (UI), 33ab92a (e2e, fixture, model fetching), 4f4582f
(gate, review, checklist), then this report.
- Captions: transcription with `multilingual=True` (language per segment),
  guard = mean word probability < 0.5 AND no speech overlap -> dropped. Punch
  (3 words, pop + karaoke, chunk starts snapped to a beat within 80 ms),
  Classic (bottom line), Minimal (lower third). Editor: per-word timing and
  confidence, click to fix, hide a line, style, on/off.
- Export: per shot from the source, the colour contract's order (fps ->
  normalise -> crop/scale -> grade chain -> captions), H.264 High yuv420p
  bt709/tv with tags; joined by stream copy; ducked music mixed; AAC 48 kHz;
  moov first; metadata stripped; checked with ffprobe and an atom scan.
  Resume: finished shots are reused by digest; a caption edit re-renders only
  its shots. Progress over `/ws/jobs`; per-stage ms in a manifest and the UI.
- "Export without music" is the edit's existing toggle; the export honours it.

## Decisions made autonomously
- Segments are H.264 crf 18 + PCM in Matroska, not ProRes; video is encoded
  once in total (amendment 019 #3). Captions burned per segment (#4).
- libass: `\{` `\}` are honoured escapes; `\\` is not (it drew `\` plus a line
  break), so a backslash is followed by U+2060. Verified on rendered pixels
  under FFmpeg 8.1 and 4.4.
- A word belongs to the shot holding its midpoint; ids are clip + recogniser
  segment + word index, so edits survive re-plans.
- Crop offsets are even (4:2:0); the crop never pads (P1). Upscaling a small
  source to the preset is allowed (scaling, not generation).
- Review render and `gather()` take a theme/edit override so the Wave 1 review
  uses Moody without writing a grading choice (as in Prompt 05).

## Assumed
- One export file per project and preset (re-export replaces it atomically).
- Punch is the suggested style; Reel the suggested format.
- Without the speech model a clip's captions are skipped with a warning; the
  export still succeeds.

## Deviations
Amendment 019 (PROPOSED): model source and digest pinning, GPU-first with CPU
fallback, H.264 segments, per-segment captions, synthesised known speech for
the WER check, the guard without a speech model, the render budget as a
criterion, metadata stripping.

## Real footage (local, not committed)
Two clips from the local test folder in a scratch library: `wave1_review.py`
made a 29.7 s Moody Reel, 7 shots, generated 101 BPM track, export 75.9 s on
the 2-core build shell, platform check clean; captions absent (no model).
Kept locally (gitignored) in `worklog/review-prompt-06-scratch/`. Seen: the
centre crop cuts the lifter at the frame edge in some shots; one shot is
briefly out of focus. The 4K HLG phone clip was not used (its proxy cannot
finish inside one 180 s shell call). One ingest pass attempted Gemini calls
because the repo's `.env` holds a key; the proxy refused the connection, so
nothing was sent; later runs blanked the key.

## Gate (verify-06-auto), 2026-10-13, Linux build shell, FFmpeg 8.1.3
**Not green here, not run as a whole.** Run one by one, each seen to finish:
5 PASS (killed at 4/8 shots; restart rendered 4, reused 4; finished files
untouched) · 7 PASS (subtitle sink: 3 tests incl. pixels; DOM: CaptionPanel
6 tests, run on a Linux mirror of `ui/`) · 8 PASS (26 tests) · 9 PASS (36) ·
11 PASS (plan guard clean). **FAIL here, environment: 1, 2, 3, 4** — the
speech-to-text model cannot be downloaded in this shell (huggingface.co is
blocked by the proxy). Without captions, `e2e_export.py --no-captions` exits 0
(6 s Reel, ~56 s) and its file passes criterion 2's check (1080x1920 h264
yuv420p bt709/tv, AAC 48 kHz, moov before mdat, 6.000 s). **Not run: 6** (60 s
of 1080p exceeds the 180 s call limit) **and 10 as a whole**; verify-05
criteria 1, 5, 9, 10 re-run: PASS. 12 [HUMAN] PENDING.
Deferred to the gate machine under amendment 016. Engine suite per file: all
41 files pass. UI: tsc, eslint, vitest 230 pass. mypy and ruff clean.
Mutation: deleting the [HUMAN] line from verify_06.sh fails 2 tests.
Hooks run by hand before every commit (Windows-only pre-commit shim, so
`--no-verify` after the guards). `make test-gpu`: no GPU code.

## Built on unverified
- STOP A (hable) and STOP B (themes): every export is graded with them.
- Prompt 05's taste checks: the export renders that edit and its ducking.
- verify-04 6/7/16, verify-05 11/12, and this gate's 1-4, 6, 10 have never run
  on this code. Caption accuracy and the music guard are untested on a real
  model; the model's digests are not pinned yet (amendment 019 #1).

## Open questions for the human
- Amendment 019: accept as written? Then pin the printed digests.
- Is Punch the right default for gym clips?

### [HUMAN] criteria
12 (every box in prompt-06.md, the Wave 1 decision) PENDING.

BUILD COMPLETE (verification pending)
