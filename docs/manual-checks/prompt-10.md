# Manual checks — the style profile, every platform, set captions (branch `feat/taste-profile`)

Only Ashwin ticks these. Deferred by amendment 016 until the build is done; the
order of the whole pass is in [VERIFY-LATER.md](VERIFY-LATER.md).

The gate measures what it can: a generated moody, fast-cut history moves every
suggestion and an empty profile moves nothing; a generated walking figure stays
in the middle third of a reel; generated squats, curls and hinges are counted
within one rep; three platform files pass the platform check. Whether the
profile's edit feels like *you* is not a number, and it is this stage's point.

## Before the review (once, from Git Bash in the repo)

1. `make setup-gpu` (adds ultralytics after torch), then
   `.venv/Scripts/python.exe scripts/fetch_models.py` (fetches the two pinned
   YOLO26 files into `$DATA_DIR/models/yolo/`).
2. Edit real sessions in `make dev` as you normally would. The profile needs
   at least three overrides per item before it changes anything ("Your style"
   shows each item as "still learning" until then). The guide asks for about
   ten sessions of your own changes; `--replay` below is only for seeing the
   page, never for this check.
3. `.venv/Scripts/python.exe scripts/profile_review.py`
   (or `--project <uuid>`). It writes `$DATA_DIR/reviews/prompt-10/index.html`:
   the same session exported as a reel with the profile off and on, side by
   side ("Play both from the start"), what each used (theme, shot length, slow
   motion, music, caption style), what the profile changed, and every learned
   item with its evidence.

## Rep labels (for gate criterion 3, once)

Pick three hero moments from real sessions (the "Set captions" list shows
them with their times). Count the reps yourself. Add one entry per moment to
`engine/tests/fixtures/rep_labels.json`: the clip's `sha256` (the library
card shows it), `start` and `end` in the clip's own seconds (as listed), and
`reps`. No path, no file name. The gate then counts with the pose model and
must land within one rep of yours.

### Profile-on is my style
- [ ] With the profile on, the edit feels more like one I would have made
      myself than the profile-off edit does
- [ ] Every line under "What the profile changed" is true of the two videos
      (theme, pace, music, caption style), and none surprises me
- [ ] Nothing I would want is missing from the choices with the profile on:
      every theme, pace and style is still offered (P2)
- [ ] "Forget" on one item in "Your style" takes only that item away, and
      switching the profile off brings back the plain suggestions

### Every platform, the person kept in frame
- [ ] "Export all platforms" gives a reel, a square and a landscape file;
      in the reel and the square I stay in frame while I move, and the crop
      glides rather than twitches
- [ ] Where nobody is in frame, the crop settles in the centre without a jump
- [ ] A scene I nudged by hand stays exactly where I put it

### Set captions say only what I confirmed
- [ ] On my real sets, the counted reps are right or off by one, and a moment
      with no clear set says so instead of guessing
- [ ] Nothing about reps or weight appears in an export until I press Confirm,
      and the caption reads exactly what I confirmed
- [ ] Nothing in the panel or the export comments on my form, body or depth
