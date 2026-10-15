# Manual checks — matching a reference clip (branch `feat/look-transfer`)

Only Ashwin ticks these. Deferred by amendment 016 until the build is done; the
order of the whole pass is in [VERIFY-LATER.md](VERIFY-LATER.md).

The gate measures what it can: a Moody round trip lands within tolerance, a
fast and a slow reference give different planner numbers, saved looks survive
a restart, the reference gets every upload refusal. Whether your edit *feels*
like the reference is not a number, and it is this stage's whole point.

Make the artifacts first, from Git Bash in the repo, after items 1-6 of the pass
(the look sits on top of STOP A's tone mapping and STOP B's grading):

1. `make dev`, open a project with real footage, drop in a reference clip
   (a video you have the right to use: one of your own older edits works),
   choose it under "Match a reference", wait for "Measuring" to finish.
2. Press "Match this reference", let the edit re-plan, export once.
3. `.venv/Scripts/python.exe scripts/reference_review.py`

The script writes `$DATA_DIR/reviews/prompt-08/index.html`: their clip and your
export side by side ("Play both from the start"), every decision one line each
(look parameters and any held at a cap, the reference's pacing and the edit's,
the caption style and what still differs), then each of your scenes under the
matched look beside the nearest reference frame, **worst match first**.

### Mine feels like theirs
- [ ] Played side by side, my edit reads as the same *look* as the reference,
      not just the same hue: contrast, shadows and colour feel alike
- [ ] The worst-matched scenes (top of the page) still look like footage
      graded on purpose, never neon, never orange or grey faces (P1 caps hold)
- [ ] The pacing feels like theirs, or the edit says plainly why it cuts
      slower (too little usable footage) instead of repeating shots to fake it
- [ ] The caption style chosen is the nearest Repcut has, and the stated
      differences are true

### Each part is mine to keep or drop
- [ ] Unticking Look, Pacing or Caption style takes only that part off and
      puts back what I had chosen before (P2)
- [ ] "Save this look as a theme" adds it to the theme selector, and it is
      still there after restarting `make dev`
- [ ] The panel's sentence about rights and downloads is true and clear
