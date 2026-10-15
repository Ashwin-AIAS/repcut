# Verify later — the deferred human pass, in order

Amendment 016 defers every `[HUMAN]` criterion, both Prompt 04 stops and every
real-machine gate run until Prompts 04–13 are built. This is that pass, in the
order to do it: an early item can invalidate later stages, so do not skip
ahead. Only Ashwin ticks a box, here or in any linked checklist.

Every gate command runs from **Git Bash** on the gate machine (real console,
the guide, the GPU), in strict mode, which turns every SKIP into a FAIL:

```
REPCUT_GATE_STRICT=1 make verify-NN
```

Minutes are estimates for one sitting, including the gate's own runtime
(verify-04 took ~27 min on 2026-09-28; each later gate nests the one before,
so gate runs grow). Artifacts live under `$DATA_DIR/reviews/prompt-NN/`,
outside the repo and outside OneDrive (they hold real footage).

Status of the build: see the newest `docs/reports/prompt-NN.md`.

---

## 1. STOP A — colour baseline and the tone-map operator (Prompt 04, branch `prompt-04`)

- [ ] Phase A boxes in [prompt-04.md](prompt-04.md) (6 boxes; needs fresh HDR
      clips shot with HDR-off twins — a fixed camera position, so the tripod)
- [ ] Operator named on the Phase A line

Look at: `$DATA_DIR/reviews/prompt-04/baseline.html` (each HDR clip beside its
HDR-off twin under every candidate operator, with numbers). Regenerate it with
`scripts/baseline_review.py` once the new clips are in the library.

**Current operator: `hable`, PROVISIONAL.** If you pick anything else:
four versions bump in one commit (proxy, scene, frame, Gemini prompt), the
library regenerates once on next open, and **every theme is re-tuned**, because
each was built on hable's picture. Criterion 1 of verify-04 fails under hable
(error 33.4 vs bound 20; mobius 13.8, reinhard 18.7 and clip 6.6 meet it); if
you keep hable, amendment 017 says what is decided then.

Time: ~60 min shooting + ~30 min review.
If this fails, affected: every stage from 04 on (all grading, every export,
the look-matching stage, the taste profile). Do this first.

## 2. Prompt 04 gate (branch `prompt-04`)

- [ ] `REPCUT_GATE_STRICT=1 make verify-NN` with NN=04 green (criteria 1, 8 and 18 depend on
      items 1 and 3)
- [ ] Criteria 6, 7 and 16 seen to run in a real console with Chrome and
      `make dev` (the scheduled build shell has no console, no Chrome, and only
      Windows builds of the venv and node_modules; it ran every other
      criterion one by one, see the report)

Time: ~45 min (Phase B adds 12 criteria, two of them through `make dev`).
If this fails, affected: 05–13 (they stack on this branch).

## 3. STOP B — grading taste (Prompt 04)

- [ ] Phase B boxes in [prompt-04.md](prompt-04.md) (needs at least one real
      HEVC/HDR clip; the phone clips in the local footage folder did not open
      in the build shell - possibly OneDrive placeholders - so none was graded)
- [ ] Gate question from `/taste-review 04` answered
- [ ] The grade panel tried in the editor: pick a theme, drag intensity, override
      one scene's light, reset; each change should show up as one taste event

Make the artifacts first (Git Bash, from the repo, after item 1):

```
.venv/Scripts/python.exe scripts/grade_review.py <sha-prefix> [<sha-prefix> ...]
```

Pass a dim-gym clip and a bright one (the "one theme across both" box) and one
HDR clip. It writes `$DATA_DIR/reviews/prompt-04/grading.html`: per clip three
scenes as corrected-only + all five themes, the suggested theme marked; a 30 s
sample under that theme; full-resolution stills for grain; face-detector hue
drift on real skin; `contact-sheet.jpg` for the phone.

Time: ~40 min. If this fails, affected: 05, 06 (every edit and export is graded),
08 (look matching starts from the corrector), 10 (taste events are its input).

## 4. Prompt 05 — the auto-edit (branch `feat/timeline-assembly`)

- [ ] Every box in [prompt-05.md](prompt-05.md) (the edit's taste, the
      controls, real speech)
- [ ] `REPCUT_GATE_STRICT=1 make verify-NN` with NN=05 green. Criterion 11 (a browser
      locks, swaps the track and removes a cut on `make dev`) and criterion 12
      (verify-04 nested) have never run to completion: the build shell has no
      Chrome, no Windows venv, and a 180 s limit per command

Make the artifacts first (Git Bash, from the repo, after items 1 and 3):

```
.venv/Scripts/python.exe scripts/fetch_models.py
.venv/Scripts/python.exe scripts/edit_review.py
```

It writes `$DATA_DIR/reviews/prompt-05/edit.html` + `edit.mp4`: a 30 s Moody
edit of your newest project, cut to the top-recommended track (generated if
your music folder is empty), with every AI decision listed and the shots sorted
weakest footage first. Drop a few of your own royalty-free tracks into
`$DATA_DIR/music/` first if you want them to compete.

Gate question: do the cuts land where *you* would have cut, or merely where
the beat is?

Time: ~45 min (review ~20, the gate ~25 with verify-04 nested).
If this fails, affected: 06 (the export renders this plan), 07 (motion moments
are placed in it), 09 (the opening is chosen from its shots), 10 (its overrides
are the taste data), 11 (the copilot drives these overrides).
## 5. Prompt 06 — captions and the export, Wave 1 (branch `feat/render-out`)

- [ ] Every box in [prompt-06.md](prompt-06.md) (would you post it; captions in
      German and English; the export on a real phone and platform)
- [ ] `REPCUT_GATE_STRICT=1 make verify-NN` with NN=06 green. Criteria 1-4 need the
      speech-to-text model and the test voices (`fetch_models.py --gate`):
      Hugging Face was blocked in the build shell, so they have **never run**.
      Criterion 6 (60 s of 1080p in under 5 min) and criterion 10 (verify-05
      nested) have never run to completion either (180 s limit per command).
      Criterion 7's DOM half needs `npm ci` in `ui/`.
- [ ] The model and voice digests printed by `fetch_models.py` copied into the
      report (amendment 019)

Make the artifacts first (Git Bash, from the repo, after items 1, 3 and 4):

```
.venv/Scripts/python.exe -m pip install -e "engine[dev]"
.venv/Scripts/python.exe scripts/fetch_models.py --gate
.venv/Scripts/python.exe scripts/wave1_review.py
```

It writes `$DATA_DIR/reviews/prompt-06/wave1.html` + `wave1.mp4`: your newest
project as a 30 s Moody Reel with captions, every AI decision and every stage's
time on one page. Re-running it reuses finished shots.

Gate question (the Wave 1 decision): **would you post this tonight?**

Time: ~70 min (model download ~5, review ~25, posting test ~10, the gate ~30
with verify-05 and verify-04 nested).
If this fails, affected: 07 (motion moments are rendered by this export), 08
(look matching exports through it), 09 (the opening's before/after is shown
from these renders), 10 (platform versions are export presets; caption and
export overrides are taste data), 12 (the quality harness measures these
files).

## 6. Prompt 07 — hero moments and slow motion (branch `feat/motion-moments`)

- [ ] Every box in [prompt-07.md](prompt-07.md) (label one real session's
      heaviest lift; judge the accepted and reverted slow shots on your own
      footage - the 0.80 confidence threshold is **PROVISIONAL**, set on
      generated motion only)
- [ ] `REPCUT_GATE_STRICT=1 make verify-NN` with NN=07 green. Criteria 2 (RIFE
      on the GPU: 3 s of 1080p under 60 s, peak VRAM) and 6 (`/health` shows
      the CUDA fields, amendment 003) have **never run**: no GPU in either build
      shell. Criterion 10 nests verify-06, so it waits for item 5's model.
- [ ] `make test-gpu` green (gpu-vram.md: GPU code touched)

Make the artifacts first (Git Bash, from the repo, after items 1-5):

```
make setup-gpu
.venv/Scripts/python.exe scripts/fetch_models.py
.venv/Scripts/python.exe scripts/slowmo_review.py
```

`make setup-gpu` installs the CUDA build of torch (~2.6 GB, once).
`slowmo_review.py` writes `$DATA_DIR/reviews/prompt-07/slowmo.html`: your
newest project's hero moments, each with its invented frame and a check frame,
accepted and reverted listed apart. Then export the project from `make dev`
and watch the slowed shots full size.

Gate question: **is anything in a slowed shot something the camera did not see?**

Time: ~60 min (torch download ~10, review ~20, labelling ~10, the gate ~20
plus verify-06 nested).
If this fails, affected: 08 (look matching exports through these shots), 09
(the opening may be a hero moment), 10 (slow-motion overrides are taste data;
platform versions re-render slow shots), 12 (the quality harness measures the
fallback and VRAM).
## 7. Prompt 08 — matching a reference clip (branch `feat/look-transfer`)

- [ ] Every box in [prompt-08.md](prompt-08.md) (mine feels like theirs; each
      part detaches and restores; a saved look survives a restart)
- [ ] `REPCUT_GATE_STRICT=1 make verify-NN` with NN=08 green. Criterion 7's UI
      half (vitest of the reference panel) has only run in a Linux copy of
      `ui/`; criterion 9 nests verify-07, so it waits for item 6.

Make the artifacts first (Git Bash, from the repo, after items 1-6): in
`make dev`, upload a reference clip you have the right to use (an older edit of
your own is ideal), choose it under "Match a reference", wait for the
measurement, press "Match this reference", export once, then:

```
.venv/Scripts/python.exe scripts/reference_review.py
```

It writes `$DATA_DIR/reviews/prompt-08/index.html`: their clip and your export
side by side, every decision one line each, your scenes under the matched look
beside the nearest reference frame, worst match first.

Gate question: **does it read as the same *look*, or just the same hue?**

Time: ~45 min (a reference and an export ~15, review ~20, the gate ~10 plus
verify-07 nested).
If this fails, affected: 10 (reference matches and detaches are taste data;
saved looks are themes the profile can learn), 11 (the copilot will call
match/detach), 12 (the quality harness measures the round trip).
## 8. Prompt 09 (branch `feat/first-seconds`) — not built yet
## 9. Prompt 10 (branch `feat/taste-profile`) — not built yet
## 10. Prompt 11 (branch `feat/talk-to-timeline`) — not built yet
## 11. Prompt 12 (branch `feat/quality-harness`) — not built yet
## 12. Prompt 13 (branch `docs/hosting-notes`) — not built yet

Each section is filled in as its prompt is built: the boxes (linking that
prompt's manual-check file), the artifacts, the command, minutes, and the
stages a failure affects.

---

## Running total

Items 1–7: ~395 min. Updated as stages are added.

## After the pass

Merge in order (04 → 13), each through `/gate NN`, tagging `v0.N.0` (Prompt 04
keeps `prompt-04-done`, amendment 015). Record every override you make into
that prompt's report: those are the taste data the profile learns from (P3).
