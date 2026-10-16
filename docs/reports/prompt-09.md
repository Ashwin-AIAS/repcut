# Prompt 09 — the opening hook, before/after, what Repcut did
PUSH PENDING: run `git push -u origin feat/first-seconds` from Git Bash (and `git push -u origin feat/look-transfer`, still pending from 2026-10-15)
Branch: feat/first-seconds (stacked on feat/look-transfer @ ed8ec3c) · Built 2026-10-16 in one scheduled run · Real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** The before side
is the proxy normalised with it; the after side is graded on top of it.

## Plan (pre-approved under amendment 016, written 2026-10-16)
- `edit/hook.py`: candidates = top-3 heroes ranked by hero score x visual
  impact over a <= 2 s window; choose on/off/manual. Planner: a teaser slot at
  timeline zero on the beat grid, the edit proper fills the rest; moved or
  duplicated by target length. Decisions, overrides, API fields, migration 0008.
- `review/compare.py`: proxy-to-preview frame mapping; `api/review.py`
  `GET /media/{id}/compare`. `review/summary.py` + `review/service.py`: the edit
  record and checked claims; `GET /projects/{id}/edit/summary`.
- UI: `HookStrip` in the edit panel, `BeforeAfter`, `WhatRepcutDid`,
  `lib/compare.ts`. Gate `verify_09.sh` / `verify_09_checks.py`.

## Built
Commits: 6d4da0b (hook, planner, decisions, overrides, API), 34803e0 (review
package, routes, dead stretches, migration 0008), 5d48bd9 (UI), 6d5aac1 (gate),
then this report, amendment 022, VERIFY-LATER item 8, CHANGELOG.
- The hook never changes the edit's length: identical to the no-hook plan on
  15/20/30/45/60 s, with and without music, three seeds (30 cases).
- Frame accuracy through the real FFmpeg chain: a generated clip whose frames
  carry their own index, proxied, then graded with Moody at 100% from a
  non-frame-aligned start (1.2345 s): both sides show the same frame at 3
  positions by frame hash. Mutation: shifting the offset by one frame fails it.
- Summary: every claim resolves in its record on 9 plans; a stale claim fails
  against a changed record; numbers move with the record.

## Decisions made autonomously (amendment 022, PROPOSED)
- Visual impact = mean movement in the window vs the clip's 95th percentile.
- Teaser at normal speed; 20 s or longer duplicates, shorter moves;
  `hook_placement` reports what really happened.
- Before = proxy, after = the scene's graded preview; captions as an
  indicator only (they burn per shot at export).
- Frame hash = hash of the frame index each side shows, not of pixels.
- "Silences removed" is reported as dead stretches left out (no movement and
  no speech), the only silence the edit removes.

## Assumed
- Candidate previews use the proxy with a media fragment (no new render).
- The summary panel refreshes on finished jobs or its Refresh button.

## Gate (verify-09-auto), 2026-10-16, laptop shell, FFmpeg 8.1, no torch, no GPU
Run whole with `--auto` (seen up to criterion 7; killed at 8 by the 180 s
limit), then 8 and 9 individually:
1 [HUMAN-bound] pending: no labelled session yet (verify-07's labels) · 1a
PASS (4) · 2 PASS (60) · 3 engine half PASS (real proxy + preview), UI half
**FAIL in the gate's own run** (repo `ui/node_modules` is the Windows
install), PASS by hand in a Linux mirror of `ui/` · 4 PASS (13) · 5 engine PASS
(6), UI same as 3 (HookStrip, EditPanel, review: 16 pass in the mirror) · 6
PASS (5 files, no network/model import) · 7 PASS (36) · 8 not run as a whole:
verify-08's criteria 1, 2, 3, 5, 6, 8 PASS, 7 engine PASS / UI env-only, 10
PASS, 9 (verify-07) needs torch; its 1a, 5, 7, 9 PASS · 9 PASS (451 files).
Engine suite: all 51 files pass (CPU). Whole UI suite 243 pass, tsc and eslint
clean (mirror). ruff and mypy clean on `engine/repcut`.
**Marked complete on those individual runs under 021 #12 / 022 #11, both
PROPOSED.** If rejected, Prompt 09 is not complete until 3's and 5's UI halves
and 8 run green on the ROG.
Commit guard: every commit through `scripts/hooks/pre-commit`; refusals: 0.

## Built on unverified
- STOP A (hable) and STOP B: the after side is the grade Ashwin has not judged.
- Prompt 07's hero ranking on real footage (labels not given): the hook is a
  hero, so a wrong hero is a wrong opening.
- Prompt 05's pacing taste checks: the teaser takes the first beats of that plan.
- Prompt 06's export and captions: the summary describes them, the slider
  only indicates captions.

## Open questions for the human
- Amendment 022: accept? Is 20 s the right line between moving and repeating
  the opening moment?
- Should the teaser ever be slowed (it is real-time now)?

### [HUMAN] criteria
None of its own. Criterion 1 is bound to verify-07's labelled session.

BUILD COMPLETE (verification pending)
