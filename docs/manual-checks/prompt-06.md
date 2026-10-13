# Manual checks — captions and the export (branch `feat/render-out`)

Only Ashwin ticks these. Deferred by amendment 016 until the build is done; the
order of the whole pass is in [VERIFY-LATER.md](VERIFY-LATER.md). This is the
end of the first wave: the question is whether the result is postable.

Make the artifacts first, from Git Bash in the repo, after STOP A and STOP B:

```
.venv/Scripts/python.exe -m pip install -e "engine[dev]"
.venv/Scripts/python.exe scripts/fetch_models.py --gate
.venv/Scripts/python.exe scripts/wave1_review.py
```

`fetch_models.py` downloads the speech-to-text model (~480 MB, once) and two
test voices for the gate, and prints their sha256 digests: paste them into the
report (amendment 019 pins them). `wave1_review.py` runs the whole loop on your
newest project: Moody (for this render only), a 30 s edit to the top track,
captions transcribed on this machine, a Reel export. It writes
`$DATA_DIR/reviews/prompt-06/wave1.html` and `wave1.mp4`, with every stage's
time. Then open the project in `make dev` for the editor boxes.

### Would you post it?
- [ ] I would post `wave1.mp4` as it is (or with only the changes I made in the
      editor below, recorded in the report)
- [ ] The grade, the crop and the captions look like one piece, not three tools
- [ ] Nothing in the picture was invented: no smeared edges, no fill, no
      changed bodies or faces (P1)

### Captions
- [ ] What I say is written down correctly, in German and in English, and the
      words appear when they are said
- [ ] No caption appears over music or noise where nobody speaks
- [ ] Punch reads well on a phone held at arm's length; Classic and Minimal are
      usable alternatives
- [ ] In the editor: fixing a word, hiding a line and switching style all show
      in the next export, and survive a re-cut of the edit

### The export
- [ ] The file plays on my phone and uploads to Instagram (or TikTok) without
      a re-encode warning, at 9:16
- [ ] Square and Landscape exports frame the lift sensibly; one per-scene nudge
      moved the crop where I wanted it
- [ ] "Export without music" gives the footage's own sound only, still cut to
      the beat
- [ ] Killing the engine mid-export (close the terminal) and exporting again
      finishes without starting over (the panel says how many shots were reused)
- [ ] A 60 s 1080p export finished in under 5 minutes on the ROG (the panel
      lists each stage's time)
