# Prompt 04 — colour baseline and grading engine
Branch: prompt-04 · Gate: Phase A at STOP A (Phase B not started) · Date: 2026-09-27

Draft at STOP A. Phase B, the operator choice and every STOP B override are
added when they happen.

## Built (Phase A)
- `ffmpeg_builder.normalise_to_sdr` + `NormalisationRecipe` (media/artifacts.py):
  one stage, one recipe object, held by both the proxy and the frame recipe.
- Proxy v2: HDR normalised to bt709 SDR, **short** side capped at 720
  (`proxy_dimensions`). `PARAMS_VERSION[proxy]` 1→2, `SCENE_PARAMS_VERSION` 1→2,
  one commit. The frame argv is byte-identical, so `FRAME_PARAMS_VERSION` stays 1.
- `repcut/fingerprints.py`: argv digests pinned per version; the scene digest
  contains the proxy's; each Gemini prompt version is pinned to the frame
  digest it was asked about (closes open issue 12 as a test).
- `repcut/freshness.py` + `POST /media/{id}/ensure-current`, called by the UI on
  clip selection: lazy regeneration, nothing enqueued while a job for the clip
  is active, never a startup sweep.
- `scripts/verify_04.sh` criteria 1–8; `scripts/baseline_review.py`.

## Decisions made autonomously
- **Criterion 1's tolerance was fixed before the fixture was measured**: v2
  error ≤ 0.30 × v1 error AND ≤ 20 codes (mean abs RGB error, 0–255). Before
  that, a first fixture with SDR white at 100 cd/m² had been measured; the
  rule was then written down, and the fixture was changed on principle to
  BT.2408 (SDR white at 203 cd/m² in HLG, what phone HLG does), not to reach
  a result. Criterion 6's tolerances (saturation 15, black 8) were set with
  those offline numbers in view.
- **Criterion 1 fails under the current operator, and I left it failing.** On
  the BT.2408 fixture: v1 74.2; hable 33.4 (0.45); mobius 13.8 (0.19);
  reinhard 18.7 (0.25); clip 6.6 (0.09). The fixture has no highlights above
  diffuse white, which penalises hable's headroom. Picking the operator is
  STOP A's decision, so neither the operator nor the rule was changed.
- **The review page lives under `$DATA_DIR`, not `docs/reviews/`.** The stills
  are real footage and the repo is in a OneDrive-redirected folder; amendment
  004 keeps `$DATA_DIR` outside sync for exactly this. `docs/reviews/prompt-04/
  baseline.html` (gitignored) is a local pointer. The script refuses to write
  if `$DATA_DIR` is itself in a sync root.
- A duplicate upload of a **stale** clip enqueues regeneration (as Prompt 02's
  upload path always did for missing artifacts); of a current clip, zero jobs.
- A failed regeneration job is retried on the next open, not suppressed; each
  attempt is visible in the jobs panel.

## Bugs fixed in earlier prompts' code
- **No gate's browser had ever loaded video data.** `cdp_browser` opened its
  page in a background tab (`visibilityState: hidden`), and Chrome defers media
  on hidden pages: every `<video>` sat at readyState 0. verify-02/03 checked
  the DOM, not playback, so this was invisible. Fixed with `Page.bringToFront`.
- `uploads._artifacts_complete` matched any kind at any current version number;
  the moved check matches each kind to its own version.

## Assumed
- "Playwright" in the kick-off read as "a real browser against `make dev`"
  (approved): `cdp_browser.py` with installed Chrome.
- HLG fixture: HEVC Main 10 via libx265 (present on FFmpeg 8.1 here; x264
  High 10 fallback coded).
- Baseline candidates: six CPU `tonemap` operators at npl 100, plus hable and
  mobius at npl 203.
- Timestamps on the review page: 20/50/80 % of the shorter clip of a pair.

## Open questions for the human
- Operator (STOP A box). If it is not hable, four versions bump in one commit:
  proxy, scene (the guard forces it), frame and Gemini prompt.
- **verify-03 criterion 17 makes live Gemini calls.** Its `make dev` stack
  inherits the developer's key, and the criterion needs a real send to see the
  disclosure step. Synthetic frames only, so no P4 harm, but it spends quota
  and breaks "zero live calls". Recommend: a local mock Gemini endpoint for
  gate stacks, in a later prompt. verify-04's own stack runs with Gemini off.

## Gate status (Phase A)
| # | Criterion | Result |
|---|---|---|
| 1 | proxy colour from the file | FAIL under hable only — tags bt709/bt709/bt709 tv yuv420p, luma 101.6 in band, SDR luma v1 125.7 = v2 125.7; error ratio 0.45 |
| 2 | one normalisation | PASS — shared object, calls [tv, pc], operator change moves both argvs |
| 3 | short-side cap | PASS — portrait 720x1280, landscape 1280x720, small 640x360 |
| 4 | versions move together | PASS — proxy 1→2, scene 1→2; negative controls 2 and 1 problems |
| 5 | stale regeneration | PASS — jobs per open 2, 0, 0; v1 file kept; scenes [1, 2]; duplicate +0 |
| 6 | a browser sees it | PASS — HDR sat 244.7 / black 16 vs SDR 251.1 / 18; both played 1.2 s |
| 7 | verify-03 green | NOT RUN to completion — the full `make verify-04` run was stopped by the host for low memory partway through this criterion; to be re-run on its own |
| 8 | [HUMAN] Phase A boxes | FAIL until signed |

`make test-gpu`: not applicable (no GPU code; torch deferred, amendment 003).

## Real library, before any regeneration
9 clips; 13 scenes under detector v1 will be re-detected and re-sent on first
open; 3 HDR clips (96 s) were never analysed. `GEMINI_DAILY_LIMIT` 1400, RPM 10.
Nothing has been regenerated.

## Risks / known gaps
- A clip opened while the engine runs an older branch regenerates under that
  branch's versions; harmless (new keys), but spends a re-send.
- The v1 proxy renderer survives only in the gate, as criterion 1's yardstick.
