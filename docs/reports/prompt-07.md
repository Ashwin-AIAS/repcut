# Prompt 07 — hero moments and slow motion
PUSH PENDING: run `git push -u origin feat/motion-moments` from Git Bash
Branch: feat/motion-moments (stacked on docs/project-scaffold @ 02c9c03, which holds feat/render-out) · Built 2026-10-14 in one scheduled run · Human checks and real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** Slow shots are
normalised with it before interpolation, like every other shot.
**Confidence threshold 0.80: PROVISIONAL** (set on generated motion only).

## Plan (pre-approved under amendment 016, written 2026-10-14)
- `highlights/detector.py`: per-segment score from movement peak, impact
  sound, scene analysis and end-of-set position; top 3 with reasons.
- `slowmo/`: `limits` (2x ceiling), `weights` (pinned, verify-then-load),
  `ifnet` (vendored RIFE 4.6), `rife` (device, flow-scale steps on OOM, CPU
  fallback, peak VRAM), `confidence` (warp error vs truth + SSIM continuity),
  `shot` (judge, then interpolate to disk), `runtime` (no torch / no weights =
  normal speed with a reason).
- Planner: heroes placed first as half-speed shots, best on the drop; a
  full-length fallback window reserved; `slowmo_on`/`slowmo_off` overrides,
  taste kinds, migration 0006. Export: slow segments from raw RGB frames.
  UI: badges, reasons, per-cut toggle. Weights via `fetch_models.py`.
- Gate `verify_07.sh` (+`--auto`), `verify_07_checks.py`, `slowmo_fixture.py`
  (generated clips), `slowmo_review.py` (contact sheet), `prompt-07.md`.
- Where: the laptop shell cannot hold torch (disk) and has no GPU, so the
  engine was built and tested in the cloud build workspace (CPU torch 2.14,
  the pinned weights, generated clips only) and committed on the laptop
  through the tracked hook.

## Built (2026-10-14)
Commits: 02c9c03 (commit guard), 5b7592d (slow-motion core), 9a6460c (heroes
in the plan), e02e640 (export), 3be8d60 (UI), e62016e (gate), then this report.
- Measured on generated 1080x1920 clips, CPU: gentle pan confidence 0.997-0.9985
  (accepted); fast barbell pan over a repeating pattern 0.213-0.218 (reverted,
  its check frame shows bent lines); mid-speed patterned pan 0.61 (reverted).
  One 1080p pair takes 2-3 s on 2 CPU cores; a 1 s slow shot ~80 s.
- Contact sheet (generated clips): `slowmo_review.py --synthetic`, kept
  outside the repo; real-footage version is VERIFY-LATER item 6.

## Decisions made autonomously (amendment 020, PROPOSED)
- RIFE 4.6, weights pinned by a digest measured here (not TOFU); weights come
  through `fetch_models.py`, not a second script.
- Confidence = worst-tenth of (leave-one-out SSIM against the real frame,
  continuity SSIM), inside a motion mask; the warp check runs first so a
  refused shot costs 12 interpolations, not the whole shot.
- Fallback window reserved at plan time; refusal never re-plans.
- Slow shots are silent; one AI slow shot per 15 s, 1-4 s long.
- The weights are verified before torch is imported (torch costs ~1 GB RSS
  for the life of the engine process).

## Assumed
- "Heaviest lift" label = clip digest + seconds in a committed fixture.
- A locked cut's speed changes only after an unlock.

## Gate (verify-07-auto), 2026-10-14, cloud build workspace, FFmpeg 6.1.1, torch 2.14 CPU
Run as a whole (`bash scripts/verify_07.sh --auto`), each line seen:
1 pending (no label yet) · 1a PASS · 2 SKIP NO_GPU · 2b PASS · 3 PASS (0.218,
normal speed, reason logged) · 4 PASS (accepted 0.9985; h264 yuv420p
1080x1920 bt709/tv, 2.20 s, platform checks clean) · 5 PASS · 6 SKIP NO_GPU ·
7 PASS · 8 PASS (47 engine tests, 7 UI) · 9 PASS · 10 and 11: see below ·
12 [HUMAN] PENDING.
11 PASS (389 files clean). **10 FAIL, environment only** (seen to finish,
~85 min): nested verify-06 failed 1-4 (no speech model: Hugging Face blocked),
6 (60 s of 1080p took 1922 s on 2 CPU cores; budget is for the ROG), 10
(verify-05: silero download refused by the proxy, no browser stack, verify-04
on FFmpeg 6.1 colour numbers and a bundle clone without the `prompt-02-done`
tag). verify-06's 5, 7, 8, 9, 11 PASS on this branch: the export, escaping,
the caption/export API and migrations did not regress. Treated as a real-machine
run deferred by amendment 016, like Prompt 06's 1-4 (amendment 020 #10).
Engine suite: all files pass with torch (cloud); the slow-motion files also
pass without torch (CI's shape). UI: tsc, eslint, vitest 232 pass. ruff and
mypy clean (with and without torch). Commit guard: every commit went through
`scripts/hooks/pre-commit`; refusals this run: only the two deliberate tests.

## Built on unverified
- STOP A (hable) and STOP B: slow shots are graded like every shot.
- Prompt 05's taste checks: heroes are placed in that plan.
- Prompt 06: verify-06 criteria 1-4, 6, 10 never ran (speech model); the
  slow segments go through that export and mux.
- The threshold on real footage, RIFE on the 3050 (speed, VRAM, OOM path on
  a real card), and the hero ranking on a real session: none observed yet.

## Open questions for the human
- Amendment 020: accept as written? Is 0.80 right once you see the sheet?
- Should slow shots keep a little of their own sound instead of silence?

### [HUMAN] criteria
1 (label) and 12 (every box in prompt-07.md) PENDING.

BUILD COMPLETE (verification pending)
