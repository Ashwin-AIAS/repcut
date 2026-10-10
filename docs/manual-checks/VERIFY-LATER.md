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

- [ ] `REPCUT_GATE_STRICT=1 make verify-04` green (criteria 1 and 8 depend on item 1)
- [ ] Criterion 6 and 7 seen to run in a real console with Chrome (the
      scheduled build shell has neither a console nor Chrome)

Time: ~35 min. If this fails, affected: 05–13 (they stack on this branch).

## 3. STOP B — grading taste (Prompt 04)

- [ ] Phase B boxes in [prompt-04.md](prompt-04.md)
- [ ] Gate question from `/taste-review 04` answered

Look at: the 5-theme contact sheet and the 30 s graded sample under
`$DATA_DIR/reviews/prompt-04/` (built in Phase B; listed in the report).
Time: ~30 min. If this fails, affected: 05, 06 (every edit and export is graded),
08 (look matching starts from the corrector), 10.

## 4. Prompt 05 (branch `feat/timeline-assembly`) — not built yet
## 5. Prompt 06 (branch `feat/render-out`) — not built yet
## 6. Prompt 07 (branch `feat/motion-moments`) — not built yet
## 7. Prompt 08 (branch `feat/look-transfer`) — not built yet
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

Items 1–3: ~155 min. Updated as stages are added.

## After the pass

Merge in order (04 → 13), each through `/gate NN`, tagging `v0.N.0` (Prompt 04
keeps `prompt-04-done`, amendment 015). Record every override you make into
that prompt's report: those are the taste data the profile learns from (P3).
