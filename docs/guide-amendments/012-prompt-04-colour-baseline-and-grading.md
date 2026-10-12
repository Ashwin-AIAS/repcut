# Amendment 012 — Prompt 04: a colour baseline phase, proxy v2, and thirteen collisions
Date: 2026-09-27
Affects: Prompt 04 (Role & Context, Deliverables, Constraints, Autonomy
Protocol, Success Criteria), Prompt 03's shipped recipes (`PARAMS_VERSION[PROXY]`,
`SCENE_PARAMS_VERSION`, the frame recipe's tone-map), Prompt 05 (inherits the
scene-boundary override), Prompt 06 (inherits the export colour contract)
Status: ACCEPTED

## What the guide says

The guide's Prompt 04 is summarised here, not quoted (amendment 006). Its
graded preview is rendered from the proxy, it runs autonomously to one visual
checkpoint at the end, and its grading package, LUT paths and adaptation keys
are named in guide terms that predate this repo. It does not say what colour
space grading happens in or produces.

## What we found

`docs/future-prompts/prompt-04-colour-baseline.md` measured the preview proxy of
real phone footage (HEVC Main 10, BT.2020, HLG, a Dolby Vision 8.4 RPU) as
**untone-mapped HDR carrying a bt709 matrix tag over bt2020/HLG primaries and
transfer**. `scale` performs the matrix conversion and silently skips the other
two. No browser tone-maps it, so it renders flat and grey.

Because the guide's graded preview is rendered from the proxy, the proxy is
not only what the eye judges — it is the grade's *input*. A corrector that
measures exposure and white balance from it measures the wrong picture; a
person judging a theme against it tunes the theme to cancel the bug; and export
(Prompt 06) grades the source, so preview and export would start from two
different pictures.

The kick-off (`docs/prompts/run-prompt-04.md`) tabulates thirteen collisions.
This amendment records each with the rule or amendment it collides with.

| # | Collision | Resolution |
|---|---|---|
| 1 | One autonomous run to a final visual stop, over a preview that is broken | Phase A (colour baseline) plus a mid-prompt human stop, STOP A. The guide's final taste stop is unchanged, as STOP B |
| 2 | The proxy tone-maps nothing and caps height, so portrait source becomes 406x720 (open issues 1, 3) | Proxy recipe v2: HDR sources through the shared normalisation function; SDR unchanged in colour; the **short** side capped at 720, never upscaled. One `PARAMS_VERSION[PROXY]` bump for both |
| 3 | Scene detection reads the proxy (008 #6), but `Scene` is keyed only by `SCENE_PARAMS_VERSION` | Bump `SCENE_PARAMS_VERSION` in the same commit as the proxy, and a guard that fails when one moves without the other |
| 4 | The tone-map operator is a look decision upstream of every theme, never judged on real footage | One named operator constant shared by the proxy and frame recipes; candidates rendered at STOP A; a changed pick bumps `FRAME_PARAMS_VERSION` and `GEMINI_PROMPT_VERSION` in one commit. HLG nominal peak re-examined |
| 5 | The guide's grading package path | `engine/repcut/grading/` |
| 6 | LUT paths "through the store", but `store.absolute()` refuses anything outside `$DATA_DIR` and built-in packs ship in the package | A pack resolver with the store's guarantee: slug-validated ids, both sides `resolve()`d, containment against the pack root, no caller-supplied path. No user-imported LUTs this prompt |
| 7 | Where LUTs come from | Generated in this repo by a committed script from committed parameters, byte-identical on regeneration. No downloaded packs; no stock preview images |
| 8 | The guide's example adaptation keys are not what analysis produces | Rules key on `lighting_temperature`, `lighting_quality` and local measurements. `vlm: null` is a normal input |
| 9 | Skin-tone checking on face-containing scenes, with no committed media and no torch (003) | CI uses synthetic skin-hue patches, light to dark, through every theme. Real-footage numbers come from a local OpenCV face-detector script, written into the review page |
| 10 | A per-scene preview budget against a real 3m11s scene (open issue 5) | The budget applies to the graded still per theme and to time-to-first-graded-frame. A full graded scene is a job with progress. The corrector samples as many **local** proxy frames as it needs |
| 11 | Open issue 11: the first prompt that reads tags owes their override | The lighting value the corrector consumed is overridable per scene, with reset, re-grade and a taste event. The boundary override is owed to Prompt 05 |
| 12 | Real clips and the human's own footage, against `testing.md`'s no-media rule | The 004 §1 split: synthetic fixtures in `verify-04`, real footage in `docs/manual-checks/prompt-04.md` |
| 13 | Output colour space is never stated | SDR bt709 in v1, for grading and export. The Dolby Vision RPU is ignored (FFmpeg decodes the HLG base layer). HDR export is out of scope |

## Why the guide's version doesn't work

**1 — A taste decision calibrated against a bug cannot be separated from it
later.** `testing.md` makes a gate the specification and `principles.md` makes
P1 ("could a viewer be shown something that did not happen") the test of every
visual feature. A theme tuned to cancel a washed-out preview bakes that
compensation into committed LUTs; once the proxy is fixed, every such theme is
oversaturated and nothing records which decisions were taste. The guide's
single final stop comes after the grades exist. The mid-prompt stop costs one
wait and makes the baseline a signed input rather than an assumption.

**2 — Two fixes to one recipe are one bump.** `media/artifacts.py` states that
a recipe change and its version bump are one edit; a bump re-encodes every
ingested clip. The tone-map and the short-side cap both change the proxy's
bytes, so taking them separately re-encodes the library twice. The short side,
not the height, is capped because portrait phone video is the common case and
`ffmpeg.md` requires rotation to be applied before dimensions are trusted — a
height cap spends the whole budget on the axis a portrait frame has to spare.
SDR sources keep their colour exactly: `ffmpeg.md` forbids an unconditional
tone-map, and their proxies are already right.

**3 — A key that does not name its input reads stale output as current.**
Amendment 008 #6 lets detection read the proxy. Tone-mapping changes the
detector's input luminance, so boundaries detected on a v1 proxy are not the
boundaries a v2 proxy produces. Without a scene bump, old rows read back as
current: open issue 11's stale-cache bug one level up. A convention to "bump
both" is what failed at Prompt 02 (the argv was right and the file was wrong),
so the coupling is a test.

**4 — The operator is taste, not correctness.** Hable, Möbius, Reinhard and the
others are all valid tone curves; which one reads as the phone's own SDR
rendering is an eye judgement against an HDR-off twin. It sits upstream of
every theme, so it is decided once, at STOP A, by the person who will judge the
themes. Since the frame sent to Gemini uses the same operator, changing it
changes that frame, and amendment 010's standing rule (a changed input ships a
`GEMINI_PROMPT_VERSION` bump) applies. `npl=100` was chosen for PQ-style
nominal peak; BT.2408 places HLG reference white at 203 cd/m², so a 203 variant
is rendered beside it.

**5** — as amendment 008 #1 and 004 §2: there is no bare `engine/` package.

**6 — "Through the store" is the guarantee, not the function.**
`security.md` requires every identifier that becomes a path to be validated
against its shape and every resolved path contained. Built-in packs are code
assets inside the installed package, which `store.absolute()` correctly
refuses. A resolver with the same three properties gives the criterion what it
asks for; bending `store.absolute()` to accept package paths would weaken the
one function every media path relies on. User-imported LUTs would add an
upload surface with a parser behind it, which this prompt does not need.

**7 — A downloaded LUT is an unverifiable licence in a public AGPL repo.**
`frontend-and-licensing.md` requires AGPL-compatible dependencies and forbids
bundling content of unverified licence. A LUT generated from committed
parameters by a committed script is ours, reviewable as numbers, and
regenerable; byte-identical regeneration makes "the LUT on disk is the one the
parameters describe" a checkable claim. Preview references rendered from the
user's own clip avoid committed media (`testing.md`).

**8 — Keys that do not exist never fire.** Prompt 03's Gemini schema produces
`lighting_temperature` (warm/neutral/cool) and `lighting_quality`, not a
colour temperature in kelvin. Rules written against the guide's example would
parse and never match. `gemini-usage.md` requires graceful degradation when
the API is unavailable, so an absent tag must produce a histogram-only
correction, not a crash.

**9 — The guide's check needs faces, and faces need media or a model.**
`testing.md` forbids committed media and amendment 003 defers torch. Skin
protection is a property of the colour transform, so it is testable on
synthetic patches of known skin hue across the lightness range, which is also a
stricter test than whatever faces happen to be in a clip. The claim on real
footage stays real by running locally with OpenCV's bundled face detector
(already a dependency), results in the review page, never in the repo.

**10 — A per-scene render budget and a three-minute scene are incompatible.**
Open issue 5 records a real 3m11s scene. What makes the UI feel live is the
selector's stills and the first graded frame; a whole scene is a render, and
`frontend-and-licensing.md` requires renders to be jobs with progress, not
spinners. P4 limits what leaves the machine, not what is measured on it, so a
long scene gets as many local measurement frames as its correction needs —
which also answers open issue 5's warning that one frame describes an instant.

**11 — P2 and the first reader of a tag.** `principles.md` P2 makes every AI
output an overridable default whose overrides re-sync dependents, and open
issue 11 assigned that debt to the first prompt that reads tags. This prompt
reads lighting tags, so it owes the lighting override. It does not cut on
boundaries, so the boundary override belongs to Prompt 05, which does.

**12** — as amendment 004 §1 and 008 #5.

**13 — Unstated means inconsistent.** `ffmpeg.md` requires colour to be set
explicitly on every encode. An unstated output space lets preview and export
disagree. v1 targets SDR bt709 because that is what every browser and social
platform renders consistently, and because the only HDR sources seen are HLG
with a DV RPU whose dynamic metadata FFmpeg does not apply. Saying HDR export
is out of scope is a decision; leaving it unsaid is a gap.

## Proposed change

**Structure.** Prompt 04 runs on one branch, `prompt-04`, in two phases.
Phase A: the normalisation stage, proxy v2, the version guards, lazy
regeneration, a local baseline review page, then STOP A — no grading code is
written until the human signs the baseline and names the operator. Phase B:
the guide's deliverables read through rows 5–11, plus migration 0003
(`taste_events`), `docs/future-prompts/prompt-06-export-colour.md`, and
`/taste-review 04`, then STOP B.

**Normalisation.** One builder function converts a source to bt709 SDR: a
`zscale`+`tonemap` chain for HDR, nothing for SDR. The proxy (tv range, x264)
and the sampled frame (pc range, mjpeg — JPEG has no limited-range convention)
both call it with one operator constant. Every grade operates downstream of it;
export (Prompt 06) must call it on the source before the same chain.

**Versions.** `PARAMS_VERSION[PROXY]` and `SCENE_PARAMS_VERSION` move together,
in one commit. The scene recipe's fingerprint includes the proxy recipe's, so a
proxy change without a scene bump fails a test. The frame recipe's fingerprint
is pinned to `GEMINI_PROMPT_VERSION` the same way (open issue 12: the Gemini
cache key does not carry the frame version).

**Regeneration.** Lazy, when a clip is opened in the UI or analysed — never a
startup sweep, which would spend the Gemini day on re-analysis. Opening a clip
calls an idempotent route that enqueues ingest and/or analysis only when the
current-version proxy or scenes are missing and no job for that clip is
pending or running. The route mutates state, so `security.md`'s browser-tab
question applies: a hostile page could trigger, once per stale clip, the same
regeneration the next open would trigger anyway — no new data leaves the
machine, and re-sends go through the existing limiter and disclosure. A bump
never deletes a v1 file (004; GC is Prompt 12).

**Fixtures.** The Prompt 03 fixture — SDR pixels with an HLG tag — stays for
tag checks. It has no honest SDR reference, so colour-accuracy criteria use a
second fixture, generated at test time: a lavfi pattern encoded to BT.2020/HLG,
10-bit, with `zscale` (HEVC Main 10 via libx265 where available, matching phone
source; x264 High 10 otherwise), whose lavfi original is the SDR reference. An
HLG round-trip through a tone-map is not an identity, so no band is chosen
after seeing a result: the v1 proxy's error against the reference is measured
first, and v2 passes only at a stated fraction of it **and** inside an absolute
sanity band. The negative control is part of the measurement.

**Browser criteria.** "Playwright" in the kick-off reads as "a real browser
against `make dev`". The repo's gates drive installed Chrome/Edge through
`scripts/cdp_browser.py`, which exists specifically to avoid a second Chromium
download. Reading the playing proxy's pixels needs `crossOrigin="anonymous"` on
the product's own `<video>`, which in turn needs every media response —
including 206 Range responses — to carry `Access-Control-Allow-Origin` for the
UI origin through the existing explicit-origin CORS configuration with
`allow_credentials=False`. No CORS widening is permitted by this amendment.

## Consequences

- Every clip ingested before this prompt is re-encoded and re-detected once,
  lazily. New scene rows mean new scene ids and therefore new Gemini calls for
  those clips; the count is reported against `GEMINI_DAILY_LIMIT` before the
  real library is touched.
- Portrait proxies are 720 wide; landscape proxies are unchanged in size.
- Any later change to the proxy recipe must bump the scene version, and any
  change to the frame recipe must bump the Gemini prompt version — both
  enforced by tests, not by memory.
- Prompt 05 inherits the scene-boundary override (open issue 11, narrowed).
- Prompt 06 inherits one contract: normalise the source with the same function,
  then the same grade chain, SDR bt709 out.
- HDR export is out of scope for v1.

## Principle check

**P1** — normalisation and grading transform captured pixels only. Grain is a
finish. Skin protection holds hue; it never smooths or retouches.
**P2 / P3** — every AI-produced grading input (theme recommendation, lighting
tag) is overridable with reset and writes one taste event.
**P4** — grading is local. Regeneration re-sends only frames whose old answer a
version bump made stale, through the existing limiter and disclosure; no new
data leaves the machine.
**P5** — no paid LUTs, no new service; LUTs are generated here.
**`testing.md` / amendment 006** — no media committed; theme descriptions are
written for the product, not transcribed.
