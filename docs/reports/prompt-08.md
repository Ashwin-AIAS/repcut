# Prompt 08 — matching a reference clip
PUSH PENDING: run `git push -u origin feat/look-transfer` from Git Bash
Branch: feat/look-transfer (stacked on feat/motion-moments @ af85bbf) · Built 2026-10-15 in one scheduled run · Human checks and real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** A reference is
normalised with it like every clip before its look is measured.

## Plan (pre-approved under amendment 016, written 2026-10-15)
- `reference/look.py`: fit a theme `Look` to the reference's colour statistics
  relative to a baseline, with `lut.py` as the forward model, inside the caps.
- `reference/rhythm.py`: shot lengths, cuts per 10 s, beat alignment, a
  frame-repeat slow-motion estimate -> `PaceSpec` (+ `slowmo_every_seconds`).
- `reference/captions.py`: local text-band detector + optional Gemini question
  on sampled frames -> nearest caption style and the differences left.
- `reference/service.py` + `jobs.py` + `api/references.py`: role, measure job,
  profile cache, match / detach per part, save look. `grading/saved.py`: saved
  looks as theme files joined to the gym pack. Migration 0007.
- UI `ReferencePanel`; gate `verify_08.sh`; review page `reference_review.py`.
- Where: the laptop shell (no torch needed). The cloud build route used for
  Prompt 07 was refused by this session's permission check.

## Built
Commits: ec3be45 (measurement, saved looks), c93c42a (roles, job, routes,
matching, migration 0007), 49b2b0f (UI), 51d9a53 (gate, review page,
checklist), f0f0199 (CORS DELETE), then this report and amendment 021.
- Moody round trip through the real FFmpeg grade (generated clip A, intensity
  100): contrast 0.242 (Moody 0.250), crush 0.042 (0.030), roll-off 0.149
  (0.150), saturation 0.788 (0.780), warmth -0.008 (-0.010), shadows 177 deg
  0.044 (190 deg 0.04). Hardstyle is outside the same tolerance (asserted).
- All five built-in looks round-trip within tolerance on generated pixels.
- Generated references through the real cut detection: 0.8 s shots -> 19
  shots, 11.2 cuts/10 s, energy 1.0, 2/2 beats; 4 s shots -> 4 shots, 1.9
  cuts/10 s, energy 0.04, 6/6 beats.
- Review page: `--synthetic` builds; the real one needs a matched project.

## Decisions made autonomously (amendment 021, PROPOSED)
- The look is fitted against a baseline: a generated neutral one for the
  cached profile, the project's footage when matching or saving.
- Pivot held, skin protection never fitted, grain not extracted; a parameter
  past a cap is held there and named (`capped`), shown in the panel.
- Saved looks: `$DATA_DIR/grading/saved/ref-*.toml`, after the pack's own.
- Caption overrides beyond the style id are listed, not applied.
- A reference is an ordinary upload marked `role=reference`; no URL route.
- Pacing gap: one warning past 1.25x the reference's median shot.

## Assumed
- Theme name defaults to "Look of <file stem>" (local only, never committed).
- Re-matching an applied part is a no-op; a theme picked after matching stays
  on detach.

## Gate (verify-08-auto), 2026-10-15, laptop shell, FFmpeg 8.1, no torch, no GPU
The whole gate cannot finish in this shell (180 s per call; criterion 9 nests
verify-07, ~85 min with torch). Run as a whole with `--auto` up to criterion 8
(seen), then each remaining criterion individually (each seen to finish):
1 PASS · 2 PASS · 3 PASS (`ref-gate-look` listed, and after a restart) · 4
[HUMAN] PENDING (7 boxes) · 5 PASS · 6 PASS (no model imports in
`reference/`) · 7: engine half PASS (23 tests), UI half **FAIL in the gate's
own run** (the repo's `ui/node_modules` is the Windows install) and PASS run
by hand in a Linux mirror of `ui/` (2 tests; whole UI suite 234 pass, tsc and
eslint clean) · 8 PASS (36) · 9 **not run**: verify-07 needs torch; its
torch-free criteria 1a, 5, 7, 9 PASS on this branch · 10 PASS (428 files).
Engine suite: all 48 files pass (CPU, no torch). ruff and mypy clean on
`engine/repcut` and the new tests. Mutation: deleting criterion 4's
`gate_human` line fails 2 tests.
7's UI half and 9 are real-machine runs (amendment 021 #12, like 020 #10).
**This build is marked complete on those individual runs under #12, which is
PROPOSED.** If #12 is rejected, Prompt 08 is not complete until 7 and 9 run
green on the ROG.
Commit guard: every commit through `scripts/hooks/pre-commit`; refusals: 1
(see the log: a relative plan path, fixed by passing it absolute).

## Built on unverified
- STOP A (hable) and STOP B: the look is measured on, and applied through,
  the PROVISIONAL normalisation and the grading Ashwin has not judged.
- Prompt 05's pacing taste checks: matched pacing feeds that planner.
- Prompt 06's export (criteria 1-4, 6, 10 never ran) and Prompt 07's slow
  shots: the side-by-side review uses that export.
- Gemini caption answers: only the stub and mocks have answered; the local
  detector is tuned on generated type, not real captions.
- Real references: none measured yet.

## Open questions for the human
- Amendment 021: accept? Should caption position/size become real overrides?
- Is "Look of <file name>" a fine default name, or ask every time?

### [HUMAN] criteria
4 (every box in prompt-08.md) PENDING.

BUILD COMPLETE (verification pending)
