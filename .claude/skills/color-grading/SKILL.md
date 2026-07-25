---
name: color-grading
description: Color science for Repcut — color spaces, LUT pipeline, per-scene adaptive grading, and reference-video color transfer with measurable deltas. Use for Prompts 04 and 08, or when a grade looks wrong or shifts between preview and export.
---

# Color grading for Repcut

Written to be readable without a color-science background. Concepts first,
then the implementation.

## The one thing that breaks everything: color space

A video file carries pixel values plus metadata saying how to interpret them —
primaries, transfer function, and **range** (limited 16–235 vs full 0–255).
Get the range wrong and the whole image shifts: blacks lift, whites clip. It is
subtle enough to look like "the grade is a bit off" rather than an obvious bug.

**Rule: set `bt709` primaries/trc/colorspace explicitly on every encode.** If
preview and export disagree on colour, this is the first thing to check.

## Why a flat LUT fails on gym footage

A single gym session has wildly inconsistent light — LED panels, window light,
mirrors, different rooms. A LUT is a fixed input→output mapping. Applied flat,
one clip looks great and the next looks broken.

So: **the theme is a target, and the grade is whatever transform gets each
individual scene there.** That per-scene adaptation is the actual feature.

## Pipeline order (order matters; these operations don't commute)

1. **Normalize exposure** — measure scene luminance percentiles, correct toward
   the theme's target.
2. **White balance** — estimate the illuminant (gray-world or white-patch),
   adapt toward the theme's target temperature. This is what makes yellow-LED
   gym footage stop looking sickly.
3. **Contrast** — lift/gamma/gain toward the theme's tone curve.
4. **Saturation** — in a perceptual space (Lab/Oklab), not naive RGB
   multiplication, which oversaturates skin tones first and worst.
5. **LUT** — the theme's stylistic signature, applied last on already-normalized
   input, which is the only way the same LUT works across clips.

## Reference matching (Prompt 08)

Start statistical, not learned. It's €0, explainable, and debuggable:

- **Reinhard transfer in Lab** — match mean and standard deviation per channel
  from reference to source. Cheap, surprisingly effective.
- **Histogram matching per channel** — stronger match, more prone to artifacts
  on clipped inputs.
- Extract the reference's *look* from multiple frames, not one — a single frame
  may be an outlier.

Only escalate beyond this if the measured delta says you must.

## Measure, never eyeball

Every grade produces a **numeric delta** to the target statistics — that is
what `verify-04` and `verify-08` assert on. Suggested metric: mean ΔE (CIE2000)
between graded output statistics and the theme/reference target, plus a
per-channel histogram distance.

Report the number. Do not claim a grade "looks good."

## The taste gate

Metrics green ≠ the grade is right. Prompts 04 and 08 end at a human
checkpoint. Produce side-by-side stills across the full lighting range —
including the worst-scoring clips first — and let Ashwin judge.

## P1 boundary

Grading is enhancement: allowed. Skin smoothing, body reshaping, sky
replacement are content alteration: banned, regardless of framing.
