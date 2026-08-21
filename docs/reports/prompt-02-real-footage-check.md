# Prompt 02 — Real-footage verification (criterion 16 groundwork)
Branch: prompt-02 · Date: 2026-08-21

**Status: the machine-checkable half of `docs/manual-checks/prompt-02.md` is
verified against three real phone clips. Every metadata assertion passes. Two
defects were found that no automated criterion can currently see, and neither
has been fixed — they are recorded here for a decision.**

This is not the session report (`prompt-02.md`) and it is not a sign-off. The
six boxes in `docs/manual-checks/prompt-02.md` remain open; the perceptual half
is a person's judgement and that file is human-signed.

## Scope and method

Three clips already ingested into one project were measured end to end: the
source bytes with `ffprobe`, the `media_blobs` row, the API response the library
grid renders from, and both derived artifacts on disk.

No filenames, absolute paths or content digests appear below
(`.claude/rules/secrets.md`). The clips are labelled by a distinguishing
property; the mapping is unambiguous from the durations.

- **Clip A** — 120fps capture, 25.5s
- **Clip B** — 31.2s
- **Clip C** — 39.5s, the longest, used for the drift measurement

**Identity was proved, not assumed.** The originals were not at the path given
for them; three files with the expected names were found elsewhere on the
machine, and each one's SHA-256 was computed and matched against the
corresponding `media_blobs` primary key, with sizes equal to the byte. The
ground truth below is therefore provably the same bytes the engine ingested.

Worth recording separately: the folder those originals were found in is
**inside a cloud-sync root**. `DATA_DIR` is correctly outside one, so nothing
the engine wrote is syncing — but P4 says footage stays on the machine, and that
source library does not.

## What the source actually is

All three are more exotic than "HEVC phone clip", and one finding below follows
directly from it:

- **HEVC Main 10, 10-bit** (`yuv420p10le`), 3840x2160 coded
- **BT.2020 primaries, HLG transfer** (`arib-std-b67`), `bt2020nc` matrix, `tv` range
- a **Dolby Vision profile 8.4 RPU** in side data
- **two audio tracks**: AAC stereo 48kHz, plus Apple `apac` spatial audio (4–5ch)
- 2–6 `mebx` timed-metadata tracks

The parser takes the first audio stream, which is the AAC one, and FFmpeg's
default stream selection picks the same — the local build has no `apac` decoder.
That agreement is luck, not design: a future FFmpeg that can decode `apac` would
select the multichannel track instead, changing the proxy's audio without a
`params_version` bump. The recipe pins no `-map`.

## Clip A — 120fps, 25.5s

| | source (ffprobe) | stored | verdict |
|---|---|---|---|
| codec | hevc (Main 10) | `hevc` | PASS |
| duration | 25.528333 | 25.528333 | PASS (delta 0.000s) |
| dimensions | 3840x2160 coded | 2160x3840 display | **PASS — differ, stored is the rotated pair** |
| rotation | -90 (display matrix) | 270 | PASS (normalised) |
| r / avg fps | 120/1 / 119.94516 | source 119.94516, normalized 30.0 | PASS — both populated, normalized is the CFR target |
| VFR | r != avg | `true` | PASS (not null) |
| audio | aac, 48000Hz, 2ch | `aac` / 48000 | PASS |

## Clip B — 31.2s

| | source (ffprobe) | stored | verdict |
|---|---|---|---|
| codec | hevc (Main 10) | `hevc` | PASS |
| duration | 31.235000 | 31.235 | PASS (delta 0.000s) |
| dimensions | 3840x2160 coded | 2160x3840 display | **PASS** |
| rotation | +90 | 90 | PASS |
| r / avg fps | 30/1 / 29.99840 | source 29.99840, normalized 30.0 | PASS |
| VFR | r != avg | `true` | PASS (not null) |
| audio | aac, 48000Hz, 2ch | `aac` / 48000 | PASS |

## Clip C — 39.5s, longest

| | source (ffprobe) | stored | verdict |
|---|---|---|---|
| codec | hevc (Main 10) | `hevc` | PASS |
| duration | 39.505000 | 39.505 | PASS (delta 0.000s) |
| dimensions | 3840x2160 coded | 2160x3840 display | **PASS** |
| rotation | -90 | 270 | PASS |
| r / avg fps | 30/1 / 29.99620 | source 29.99620, normalized 30.0 | PASS |
| VFR | r != avg | `true` | PASS (not null) |
| audio | aac, 48000Hz, 2ch | `aac` / 48000 | PASS |

The API response matches the `media_blobs` row field for field on all three, and
`has_proxy` / `has_thumbnail_strip` are true for all three. Measured by booting
the engine on a spare port, issuing one GET, and stopping it; the database was
read, never written.

## VFR was verified at frame level, not taken on the heuristic's word

`r_frame_rate != avg_frame_rate` can be a false positive on near-CFR footage, so
every inter-frame gap was histogrammed. All three are genuinely variable:

| clip | baseline gap | outliers |
|---|---|---|
| A | 8333/8334us (1/120) | 32 gaps of 8750us, 4 of 7917us |
| B | 33333/33334us (1/30) | 5 gaps of 35000us, 4 of ~31666us |
| C | 33333/33334us (1/30) | 5 gaps of 35000us, 2 of 31666us |

The 33333/33334 alternation is timebase rounding, not variance. The 35000us and
31666us gaps are the real thing. Nothing stored `null`; the column is telling the
truth about all three.

## Finding 1 — the A/V drift metric measures the wrong quantity

Criterion 6 computes `abs(last video packet end - last audio packet end)`
**within the proxy**, against a 40ms budget, and reads 17.1ms on the synthetic
VFR fixture. Applied unchanged to real footage:

| clip | gate metric on proxy | same metric on **source** | introduced by ingest |
|---|---|---|---|
| A | 1.3 ms | 6.3 ms | -5.0 ms |
| B | **258.7 ms** | 260.3 ms | +1.7 ms |
| C (longest) | **248.0 ms** | 253.0 ms | +5.0 ms |

Read literally, the longest clip is 6x over budget. Read correctly, the pipeline
is sound: **the phone's audio track already ends 253ms before its video track in
the original file**, and ingest carries that forward while adding 5ms.

So the metric measures the source's own track-length mismatch, not drift. The
two are equal only on a fixture whose tracks are generated to the same length —
which is the only input it has ever had. **Criterion 6 is green today and would
read 248ms the first time it met real footage.**

Supporting evidence that no real drift exists: audio packet count and end time
are preserved almost exactly through the encode (1842 -> 1841 packets,
39.252000 -> 39.252000), video end moves 39.505 -> 39.500, and both streams start
at 0.000 in source and proxy alike.

The honest measurement is the proxy's A/V offset *relative to the source's*,
which is **5.0ms on the longest real clip, against a 40ms budget**. Not changed:
a gate's metric is not something to rewrite without a decision.

## Finding 2 — half of the requested colour is not written into the file

The proxy recipe sets four colour values explicitly. Two land, two do not, on all
three proxies:

| requested | in the file |
|---|---|
| `-colorspace bt709` | `bt709` — PASS |
| `-color_range tv` | `tv` — PASS |
| `-color_primaries bt709` | **`bt2020`** — FAIL |
| `-color_trc bt709` | **`arib-std-b67`** (HLG) — FAIL |

Confirmed to be more than a container atom: stripping the MP4 and probing the raw
H.264 bitstream shows the same values in the VUI. Reproduced standalone with the
exact recipe argv on a two-second slice, so it is the recipe meeting this input
rather than a one-off.

**Cause.** `scale` can convert a YUV matrix but cannot convert primaries or
transfer. The matrix conversion is real, not a relabel — encoding with and
without `-colorspace bt709` produces different pixels. Primaries and transfer get
no conversion, so the pixels stay BT.2020/HLG and FFmpeg tags them truthfully,
overriding the flags.

**Consequence.** The proxy is an HDR clip in an SDR container carrying a mixed
tag set that describes no real colour space, and no browser will tone-map it. The
preview a person scrubs will look flat and desaturated against the phone's own
playback — that is the pipeline, not their eyes, and it should be known before
anyone judges a perceptual box. The thumbnail strip sets no colour flags at all
and inherits the same problem.

Converting properly needs `zscale`/`tonemap` or `libplacebo` in the filter graph.
That is a recipe change and therefore a `params_version` bump — **Prompt 04
territory**, and not touched here.

## Derived artifacts

**Proxies.** All three 406x720, h264, `yuv420p`, `r_frame_rate == avg_frame_rate
== 30/1`. CFR confirmed at frame level, not only in metadata: the only gaps
present are 33333 and 33334us, which is 1/15360-timebase rounding. Durations
25.534 / 31.233 / 39.500 — within 6ms of source, well inside the 0.1s tolerance.
Audio `aac` 48000Hz stereo on all three, at the project rate. Clip A's 3062
source frames correctly decimate to 766.

One small artefact: 2160x3840 at height 720 is exactly 405 wide, and `scale=-2`
rounds up to 406 — a 0.25% horizontal stretch in the preview.

**Thumbnail strips.** Frame count equals `ceil(duration / seconds_per_frame)`
exactly in all three cases — 13 / 16 / 20 — and every tile is 102x180, portrait.
PASS on both halves.

## Recorded elsewhere

The proxy recipe's height cap lands on the long side for portrait source, which
is why the preview is 406px wide. That observation is in `prompt-02.md` under
*Open issues*, flagged as Prompt 05 territory, with the `params_version` and
re-encode cost stated as the reason not to move it now.

## What this does not cover

- **Nothing here is a tick.** All six boxes in
  `docs/manual-checks/prompt-02.md` remain open. Metadata equality, artifact
  shape and timing are measurable and are measured; whether the thumbnail looks
  right, whether the proxy scrubs smoothly and whether a sync cue lands where it
  should are not, and are the human's.
- **The duplicate-upload box is not yet exercisable.** One project exists, and
  `media_files` has a unique `(project_id, sha256)`, so re-dropping a clip into
  the same project cannot test dedup. A second project is needed. The extra
  ingest jobs visible in the table are Re-ingest calls, not failed dedup — each
  ran 0.34s and created no new artifact rows, which is the reuse path working.
- **Neither finding above is fixed.** Both change either a gate's metric or an
  artifact's bytes, and both are decisions rather than repairs.
