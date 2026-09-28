# Amendment 013 — every colour conversion stated in the filter graph
Date: 2026-09-28
Affects: Prompt 04 (amendment 012's normalisation stage and proxy v2), Prompt
03's shipped frame recipe, Prompt 06 (inherits the export colour contract)
Status: PROPOSED

Supersedes one decision of amendment 012, which is not edited.

## What the guide says

Amendment 012, row 2: "Proxy recipe v2: HDR sources through the shared
normalisation function; SDR unchanged in colour". Its resolution text:
"SDR sources keep their colour exactly: `ffmpeg.md` forbids an unconditional
tone-map, and their proxies are already right." And under **Normalisation**:
"One builder function converts a source to bt709 SDR: a `zscale`+`tonemap`
chain for HDR, nothing for SDR."

`.claude/rules/ffmpeg.md`: "Preserve or explicitly set `bt709` + range on
every encode."

## What we found

The PR for prompt-04 failed CI's "Engine (Python)" job on four cases of
`test_sampled_frame_decodes_to_the_sources_colours_as_jfif_says`. CI runs
Ubuntu's FFmpeg 6.1.1; this laptop runs 8.1. Reproduced exactly on a 6.1.1
build (same numbers as the CI log).

1. **The CI failure was the test's instrument, not the frame.** The helper
   that read "samples as stored" asked FFmpeg for `yuv444p`. FFmpeg 6.1 decodes
   a JPEG as `yuvj420p` and treats `yuvj420p -> yuv444p` as a full-to-limited
   range conversion; 8.x carries range as frame metadata and does not. Every
   JPEG, including one produced by 8.1, read 16 codes toward grey through 6.1's
   reader and correctly through 8.1's. Read in the stream's native pixel
   format (no scaler), frames from both versions were 1.1-3.8 codes from truth.

2. **Checking the hypothesis behind the report found a real defect the tests
   did not cover: proxy v2 depended on the FFmpeg version.** v2 has no colour
   filter for SDR and writes `-colorspace bt709 -color_range tv`. From FFmpeg
   7.1 the CLI converts toward those encoder settings; 6.1 only relabels.
   Measured on bars, same argv, decoded as the proxy's tags say:

   | Source | 6.1.1 | 8.1 |
   |---|---|---|
   | bt709 / tv | 0.6 | 0.6 |
   | smpte170m / tv | **30.0** | 3.8 |
   | bt709 / pc | 2.0 (by the `yuvj` accident) | 1.5 |

   "Their proxies are already right" held only on 7.1+.

3. **The HDR chain's first stage read the source's matrix and range from the
   decoder's frame properties** (amendment 012 recorded this as "left to
   zscale's input auto-detection"). An untagged HDR matrix would have reached
   zimg as "unspecified".

4. **Explicit conversion is version-independent with either engine**, but
   zimg is bit-stable across versions and swscale is not (full-range bars:
   zimg 2.0 on both, swscale 2.0 vs 1.5), and zimg is closer on BT.601 (1.8 vs
   3.8). `zscale`'s constant tables are identical on 6.1.1 and 8.1 and accept
   ffprobe's own spellings.

## Why the guide's version doesn't work

"Nothing for SDR" is not nothing: it hands the conversion to the CLI, whose
behaviour changed between the release CI runs and the release the laptop
runs. The preview a person judges and the export Prompt 06 builds must not
depend on which FFmpeg the user happens to have. Pinning CI's FFmpeg to 8.x
would hide the dependency, not remove it.

## Proposed change

Replace amendment 012's normalisation sentence with:

> **Normalisation.** Every change of colour encoding is one `zscale` stage
> stating matrix, range, primaries and transfer for its input and its output
> (`ffmpeg_builder.convert_colour`); no stage reads them from frame
> properties and no encode relies on the CLI to convert toward its tags. HDR
> sources go through the `zscale`+`tonemap` chain, each stage stating its
> input. SDR sources get one stage from their own matrix and range to the
> working space, with primaries and transfer stated equal on both sides, so it
> is never a gamut or curve change and never a tone-map. Tags reach the graph
> only through an allow-list; untagged reads as bt709, limited range for SDR
> and as BT.2100 for HDR.

and row 2's "SDR unchanged in colour" with "SDR unchanged in colour on FFmpeg
7.1+, and now on every version".

Minimum FFmpeg 5.1 (`-fps_mode`, used by every proxy), with `zscale` and
`tonemap` present, checked by `scripts/check_env.py`.

## Consequences

- `PARAMS_VERSION[proxy]` 2→3, `SCENE_PARAMS_VERSION` 2→3,
  `FRAME_PARAMS_VERSION` 2→3, `GEMINI_PROMPT_VERSION` 3→4, pinned in
  `repcut/fingerprints.py`, whose digests now cover four colour branches.
- On FFmpeg 8.1 the new proxies are byte-identical to v2's for real HLG, a
  synthetic HLG fixture and a tagged bt709 SDR clip: STOP A's look is
  unchanged. Frames differ by a mean of 0.6-0.8 codes (swscale to zimg).
- The real library is still held at v1 (STOP A), so the bumps cost no extra
  regeneration or Gemini re-send beyond the one already planned.
- Every proxy now needs libzimg, not only HDR ones.
- Prompt 06 inherits the rule: export calls the same helper.
- No passed gate is invalidated. verify-04 criterion 1's SDR luma check
  measures the same bytes on 8.1.

## Principle check

P1: colour encoding only; no gamut, curve or content change for SDR, and the
HDR chain is unchanged in effect. P4: the frame sent to Gemini changes by under
two codes and nothing more is sent. P5: libzimg is free and already required.
No principle is bent.

## Correction, same day, before review

"Minimum FFmpeg 5.1" above was the floor from the release notes, and measuring
it proved it wrong. On 5.1 the CPU suite ran 487 passed, 3 failed: every
rotation test, because the fixture that stamps rotation uses
`-display_rotation`, which first shipped in 6.0. So rotation was never verified
below 6.0. 5.0.1 fails as predicted (`Unrecognized option 'fps_mode'`). The
floor in `check_env.py` is therefore **6.1**, the oldest release the whole
suite has passed on (CI's 6.1.1 and a Windows 6.1.1 build). The text above is
left as written, per the amendment rule.
