# Manual checks — hero moments and slow motion (branch `feat/motion-moments`)

Only Ashwin ticks these. Deferred by amendment 016 until the build is done; the
order of the whole pass is in [VERIFY-LATER.md](VERIFY-LATER.md).

This stage has objective gates, but two things only a person can supply: the
label the hero-detector criterion asserts against, and a look at the
confidence threshold on real footage. The threshold (0.80) was set on
generated motion only: the build workspace never received your footage (P4).

Make the artifacts first, from Git Bash in the repo, after items 1-5 of the pass:

```
make setup-gpu
.venv/Scripts/python.exe scripts/fetch_models.py
.venv/Scripts/python.exe scripts/slowmo_review.py
```

`fetch_models.py` downloads the RIFE 4.6 weights (21 MB, once, digest-pinned).
`slowmo_review.py` takes your newest project's top hero moments and writes
`$DATA_DIR/reviews/prompt-07/slowmo.html`: per moment, two captured frames,
the invented frame between them, and the check frame (a frame the camera did
record, invented from its neighbours, beside the real one), with the
confidence, accepted and reverted listed apart. Then export the project once
from `make dev` and watch the slowed shots at full size.

### The label (criterion 1)
- [ ] One real session labelled: in `engine/tests/fixtures/hero_labels.json`,
      add `{"sha256": "<the clip's digest>", "heaviest_lift_seconds": [start, end]}`
      for the clip with your heaviest lift (the digest is on the clip's card in
      the library; seconds are in the clip's own time). Commit it: it holds no
      path, no name and no footage
- [ ] `make verify-07` criterion 1 then shows the lift's rank in the top 3

### Slow motion on your footage
- [ ] Nothing in an accepted slow shot shows something the camera did not
      see: no bent bar, doubled plate, smeared arm or melting edge (P1)
- [ ] Every reverted moment is one where you can see why in its check frame
      (or say which ones were refused without reason: the threshold is then
      too strict)
- [ ] The moments ranked 1-3 are moments you would slow down yourself
- [ ] Badges, reasons and the per-cut "Slow" / "1x" toggle in the editor do
      what they say, and a locked cut refuses a speed change
- [ ] A slowed shot is silent under the music, and that is how you want it
