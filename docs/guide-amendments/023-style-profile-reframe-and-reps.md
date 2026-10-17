# Amendment 023 — the style profile's rules, tracking without a GPU, and what a rep count may claim
Date: 2026-10-17
Affects: Prompt 10 (Deliverables 1-6; Constraints; Autonomy Protocol; Success Criteria), `scripts/fetch_models.py`, `make setup-gpu`
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). Three independent pieces: a style
profile learned from the person's overrides that the recommenders read,
shown and resettable per item; reframing that follows the subject for three
aspect ratios from one edit, exported in one click; and rep counting by pose
estimation on hero moments, turned into set captions with the exercise and a
weight the person enters, confirmed before burn-in. Priors, not rules; cold
start unchanged; every influence explainable; smooth crops with a centre
fallback; pose on hero moments only; no judging of bodies. Prove it with a
synthetic taste history, a walking clip, three labelled sets, the platform
checks and a profile-off/on review.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | "Per-user profile" in a single-user, local app | One profile per installation (the person at the keyboard), recomputed from `taste_events` on every read; only its switch and per-item reset times are stored (`style_profile`, one row). It can never drift from its evidence |
| 2 | How a profile is learned, explainably | Tallies, not a model (`profile/learn.py`): theme +1 chosen / -0.5 passed over, x0.08; intensity mean of chosen - suggested; caption style like theme; music energy mean of chosen - suggested track energy. Each shrunk by n/(n+3), bounded (theme 0.35 on 0.1-0.7 scores, intensity 20 points, music 0.25), **active only at 3 overrides**. Every item carries its sentence and count; the edit, the grade suggestion and the panel show them |
| 3 | What counts as a pacing signal | Only overrides about pace: slow motion turned off (+) or asked for (-), and a matched reference's pacing (its energy). A theme change does not count: picking Moody for its colour is not asking for Moody's long shots. Consequence: pacing learns slowly from today's UI; Prompt 11's copilot ("cut faster") should log a pacing event |
| 4 | Where the profile applies | Theme suggestion (prior added to every score; with no lighting tags the favourite replaces the pack default), the edit's theme when none was chosen, pace (shot lengths x(1-0.4·bias), never under a beat; a matched reference's pacing wins, it is explicit), music ranking (wanted energy shifted), default intensity, default caption style. Cold start or off: `StyleProfile()`, whose every method is the identity, so "Wave 1 behaviour" is exact (tested over every tag combination and through the engine) |
| 5 | "Subject-tracked reframing via YOLO" on a machine without torch | Two detectors behind one interface (`export/subjects.py`): the pinned YOLO26n person detector when torch + ultralytics + weights are present, else OpenCV background subtraction (the largest moving region; nothing when the camera itself moves). The mandatory CPU path (gpu-vram.md); its track is stored under its own name so installing YOLO later re-tracks |
| 6 | Smoothing and fallback | Zero-phase Gaussian (σ 0.35 s) over per-frame targets, a pan ceiling of 0.9 crop widths per second, even keyframes every 6 frames interpolated by FFmpeg's `crop` (per-frame `x` in `n`). Missing > 1 s: the target is the centre, reached through the same smoothing; seconds lost are logged and summed into an export warning. Slow shots keep a static crop (interpolation reads a fixed window) |
| 7 | "Manual keyframe override per scene" | The existing per-scene placement is the manual keyframe: a scene placed by hand keeps exactly that placement and is not tracked (P2). Multi-keyframe editing is not built |
| 8 | Which pose model | YOLO26n-pose (the "current Ultralytics pose model"), weights pinned by sha256 measured on download (Ultralytics assets v8.4.0); detection uses YOLO26n. `fetch_models.py` fetches both; `make setup-gpu` installs ultralytics after torch. AGPL-3.0 like Repcut (`docs/licenses/yolo.md`). Ultralytics unpickles `.pt`; Repcut passes a file only after its digest matches the pin at that moment |
| 9 | Rep counting method | The joint that moves most (elbow, knee or hip, both sides averaged where visible), 0.2 s smoothing, hysteresis at 30% from each end of the segment's own 5-95% range, ≥ 25° amplitude, ≥ 0.5 s per rep. No visible person or no repeated movement: no number, a sentence. Hero segments only, at most 60 s, keypoints streamed and dropped; one model resident |
| 10 | "Weight from ONE user tap per set" | One typed field per set (kg or lb). No plate reading. The exercise comes from the scene analysis's existing per-scene guess (no new frame sent, P4) |
| 11 | Confirmation and burn-in | `gym_sets` rows keep the counter's number apart from the person's. Only confirmed sets reach the export: one caption per set on the first shot cut from its segment (the teaser only if nowhere else), top of the frame so speech captions stay readable. Confirm and dismiss are taste events (migration 0009) |
| 12 | "Name the product boundary in code" | `gym.reps.PRODUCT_BOUNDARY`, returned by the API and shown under the set captions; a test refuses form, physique or body-scoring names in the package |
| 13 | Criteria a build shell cannot run | Criterion 3 (three labelled real sets) is pending until `rep_labels.json` holds three segments, amendment 017's pattern; 3b (both models on the GPU under 3.2 GB) SKIPs as NO_GPU; generated sets (3a: squat 8, curl 5, hinge 12, with noise, dropped frames and tempo changes) and a generated walking figure run everywhere. The `[HUMAN]` review's artifact is built by `scripts/profile_review.py`; with too little real history it refuses unless `--replay` (synthetic, for the page's shape only) |
| 14 | Gate in this build shell | As in amendment 021 #12 / 022 #11: the UI half of criterion 8 and the nested verify-09 UI halves run only on the real machine from this shell (the repo's `ui/node_modules` is the Windows install); they passed in a Linux copy of `ui/` |

## Principle check

P1: crops never leave the frame and never pad; nothing is generated; the
counter reads movement and draws nothing but the confirmed text. P2: the
profile nudges, never removes; it can be switched off and forgotten per item;
a hand-placed scene is never moved; a rep count is a suggestion until
confirmed. P3: the profile is built from taste events, and the new set
captions log their own. P4: tracking and pose run on the local proxy; only
numbers are stored; no new frame goes to Gemini. P5: ultralytics and the
YOLO26 weights are AGPL-3.0 and free; nothing paid.
