# Prompt 04 — the preview is not colour-accurate yet

Recorded 2026-08-21, during Prompt 02. **Read this before writing Prompt 04's
kick-off.**

## The blocker

Prompt 04 is colour grading, and it is a **HUMAN REVIEW taste checkpoint** —
its output is judged by eye, against the preview, by a person deciding whether a
grade looks right.

The preview is currently not colour-accurate. Measured on three real clips:

| | source | proxy |
|---|---|---|
| primaries | `bt2020` | `bt2020` |
| transfer | `arib-std-b67` (HLG) | `arib-std-b67` |
| matrix | `bt2020nc` | `bt709` |
| range | `tv` | `tv` |
| pixel format | `yuv420p10le` | `yuv420p` |

The footage is HLG BT.2020 HDR with a Dolby Vision profile 8.4 RPU. The proxy
recipe asks for a bt709 conversion on all three of primaries, transfer and
matrix; FFmpeg performs the matrix conversion and silently skips the other two,
because `scale` cannot do them. The proxy is **untone-mapped HDR** carrying an
incoherent tag triple, and no browser tone-maps it, so it renders flat and
desaturated.

Full detail, including how it was verified, is in
[`../reports/prompt-02.md`](../reports/prompt-02.md) under *Open issues*.

## Why this blocks Prompt 04 specifically rather than merely annoying it

**A grade tuned against a wrong preview is wrong twice.** The person grading
would be compensating for the missing tone-map — pushing saturation and contrast
to counteract a washed-out preview — and that compensation is baked into the
theme. The moment the conversion is fixed, every grade built against the old
preview is oversaturated, and there is no record of which decisions were taste
and which were correction.

This is worse than an ordinary open bug because the checkpoint produces
*artifacts that persist*: LUTs, theme definitions, reference-match parameters.
A rendering bug can be fixed later; a taste decision calibrated against it
cannot be distinguished from a real preference after the fact.

## What Prompt 04's kick-off must decide first

1. **Fix the proxy conversion before any grading work**, or **grade against
   something other than the proxy** — a source-decoded still, correctly
   converted, is enough to judge a LUT against. Either is defensible; leaving it
   undecided is not.
2. If the recipe is fixed: it is a `params_version` bump and a re-encode of
   every ingested artifact, and it should be taken together with the proxy
   height-cap fix recorded in the same *Open issues* section. Two bumps for two
   fixes to one recipe is wasted work.
3. **The grade must be verified end to end, not only in preview.**
   `.claude/rules/ffmpeg.md` requires colour to be preserved or explicitly set
   on every encode. This defect is exactly the failure that rule exists to
   prevent, and it survived because nothing asserted the *output file's* colour
   metadata — the builder's argv was snapshot-tested, and the argv was right.
   Prompt 04's gate should probe the rendered file, not the command.

## Related

- [`prompt-03-frame-source.md`](prompt-03-frame-source.md) — the same HDR source
  degrades frames sampled for Gemini, for a related but separate reason.
