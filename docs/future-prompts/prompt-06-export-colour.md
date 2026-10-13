# Export colour: the contract the export stage inherits from grading

Written while building the grading engine (amendment 012, Phase B). The export
stage must make the exported file look like the graded preview the person
approved. Preview and export start from different bytes (the 720p proxy and
the full-resolution source), so the only way they agree is one pipeline, in
one order, with every colour conversion stated (amendment 013).

## The order

1. **Normalise the source**: `ffmpeg_builder.normalise_to_sdr(...)` with the
   same `NormalisationRecipe` the proxy uses (`media/artifacts.NORMALISATION`).
   HDR (HLG/PQ, BT.2020) is tone-mapped to bt709 SDR; an SDR source gets only
   the `convert_colour` stage to the tv-range working space, never a tone-map.
   The Dolby Vision RPU is ignored: FFmpeg decodes the HLG base layer.
2. **Scale** to the export size after normalising, never before (the tone-map
   reads full-resolution pixels; scaling first changes what it sees).
3. **The same grade chain**: `ffmpeg_builder.build_grade_filter(GradeSpec)`,
   with the `GradeSpec` built by `grading.apply.grade_spec` from the scene's
   stored correction, theme, intensity and LUT (`packs.ensure_lut`). Per scene:
   the correction is per scene, so export renders per scene (or per-scene
   `sendcmd`/segment boundaries) exactly where the preview did.
4. **Encode bt709 SDR, tv range, yuv420p**, tags stated on the encode
   (`-colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv`)
   and checked on the rendered file with ffprobe, not on the argv.

`scripts/grade_review.py`'s `full_res_still` is a working reference for steps
1 and 3 on one frame.

## What export must not do

- Re-measure the correction at full resolution: the person approved the
  correction measured on the proxy; a second measurement is a second answer.
  Read the stored values (or recompute them from the proxy with the same
  inputs, which is deterministic).
- Apply the grain before scaling: grain is sized for the output, so it is the
  last stage (it already is in `build_grade_filter`; keep scaling before it).
- Export HDR. v1 is SDR bt709 out (amendment 012 row 13).

## Gate ideas for the export stage

- A graded export of a fixture and the graded preview of the same scene,
  both scaled to 640 wide, agree within a stated mean error on decoded pixels.
- Intensity 0 export equals the corrected-only export on decoded pixels.
- ffprobe tags on the exported file: bt709 triple, tv, yuv420p.

## Built on unverified

The tone-map operator (hable) is PROVISIONAL until STOP A is signed. A
different operator changes step 1 for preview and export alike; nothing here
changes, but every theme is re-tuned on top of it.
