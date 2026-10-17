# Prompt 10 — the style profile, every platform, set captions
PUSH PENDING: run `git push -u origin feat/taste-profile` from Git Bash (and `feat/look-transfer`, `feat/first-seconds`, still pending)
Branch: feat/taste-profile (stacked on feat/first-seconds @ 3b40873) · Built 2026-10-17 in one scheduled run · Real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** Every export here
is graded on top of it.

## Plan (pre-approved under amendment 016, written 2026-10-17)
- Track A: `profile/` (types, tallies in `learn.py`, `service.py`, `replay.py`);
  migration 0009; the grade suggestion, the edit's theme and pace, music
  ranking, default intensity and caption style read it; `/profile` routes;
  "Your style" panel.
- Track B: `export/subjects.py` (YOLO person detector or movement),
  `export/tracking.py` (cached track), `reframe.crop_path` (smoothed, keyed
  crop), `CropBox.x_keys` in the builder; `/export/all` job; UI button.
- Track C: `gym/reps.py` (pose, joint-angle counter), `gym/captions.py`,
  `gym/service.py`, `gym/jobs.py`, `/gym` routes; set captions in the caption
  panel; confirmed sets burned in at export.
- Gate `verify_10.sh` / `verify_10_checks.py`; `scripts/profile_review.py`.
Built inline in one session (no subagents: the tracks share the export path).

## Built
Commits: a2d5a5e (profile, migration 0009), 0c7da6c (tracking, keyed crops,
export all, YOLO pins), 6262a22 (reps, set captions), 9b1b266 (UI), 1f09014
(profile review), 8260807 (gate), 21ff4e0 (amendment 023, licences, changelog).
- A generated moody, fast-cut history (10 sessions) moves Moody up in 16 of
  24 lighting cases (level in the other 8, never down), makes it the untagged suggestion with its
  sentence, shortens shots, raises intensity and switches captions to Classic;
  harsh light still picks Hardstyle. No events, or the profile off: identical
  to before (every tag case; the same shots through the engine).
- Generated walking figure, tracked by movement, rendered through the real
  FFmpeg crop: subject in the reel's middle third in >= 95% of frames; a
  still clip and a lost subject return to the centre without a jump.
- Generated squat (8), curl (5) and hinge (12) with noise, dropped frames and
  tempo changes: all within one rep. Three platform files at 1080x1920,
  1080x1080, 1920x1080 pass the platform check.

## Decisions made autonomously (amendment 023, PROPOSED)
- Profile = tallies with evidence, active at three overrides, bounded; one per
  installation, recomputed on every read; only switch and resets stored.
- Pacing learns from slow-motion toggles and matched reference pacing only.
- Movement tracker as the CPU path; a hand-placed scene is never tracked.
- YOLO26n / YOLO26n-pose, sha256-pinned; ultralytics via `make setup-gpu`.
- Set captions: one per set, top of frame, confirmed only.

## Assumed
- Exercise names come from the existing scene guess; nothing new is sent.
- `profile_review.py` refuses without an active real profile unless `--replay`.

## Gate (verify-10-auto), 2026-10-17, laptop shell, FFmpeg 8.1, no torch, no GPU
The whole `--auto` run was seen through 3b (killed by the shell's 180 s limit
during 4); the rest was run criterion by criterion:
1 PASS (4) · 1b PASS (7) · 2 PASS (7) · 3 [HUMAN-bound] pending: 0 of 3 rep
labels · 3a PASS (6) · 3b SKIP NO_GPU · 4 PASS (2, 143 s) · 5a PASS · 5 [HUMAN]
pending (shown pending in a cut-down run; FAIL in full mode) · 6 PASS · 7 PASS
(14 files) · 8 engine PASS (3), UI half **FAIL in the gate's own run**
(Windows `ui/node_modules`), PASS in a Linux mirror (32) · 9 PASS (36) · 10
not run whole: verify-09's 1a, 2, 4, 6 PASS, 3 and 5 engine halves PASS / UI
env-only · 11 PASS (479 files).
Engine suite: all 55 files pass on CPU (`test_uploads.py` hangs in about one
run in three here, at 3b40873 too: not this prompt's). Whole UI suite 253
pass, tsc and eslint clean (mirror). ruff and mypy clean on `engine/repcut`.
**Marked complete on those individual runs under 021 #12 / 022 #11 / 023 #14,
all PROPOSED.** If rejected, Prompt 10 is not complete until 8's UI half and
10 run green on the ROG.
Commit guard: every commit through `scripts/hooks/pre-commit`; refusals: 0.

## Built on unverified
- STOP A (hable) and STOP B: both review edits are graded on them.
- Prompt 07's heroes on real footage: rep counting and set captions run on them.
- Prompt 06's export on the real machine: all three platforms reuse it.
- YOLO26 on the 3050: never loaded here (no torch, no GPU); VRAM unmeasured.
- The movement tracker on real gym footage (gym backgrounds move: mirrors,
  other lifters); only generated clips were tracked.

## Open questions for the human
- Amendment 023: accept? Is three overrides the right bar for "your style"?
- Should the copilot (Prompt 11) log "cut faster/slower" as pacing evidence?

### [HUMAN] criteria
- 5 Profile-on is my style (docs/manual-checks/prompt-10.md): PENDING.
- 3 Reps within one on three labelled real sets: PENDING (labels needed).

BUILD COMPLETE (verification pending)
