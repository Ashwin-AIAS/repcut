# Prompt 03 — sample frames from the source, never from the proxy

Recorded 2026-08-21, during Prompt 02, while the measurement was fresh.

## The trap

Prompt 03 samples **one frame per detected scene** and sends it to Gemini
(P4, `.claude/rules/gemini-usage.md`). It will be looking for a frame to send,
and there will be two files available for every clip: the source, and the proxy.

The proxy is the wrong one, and it is the one a scene-sampling step reaches for,
because it is already small, already local, already decoded fast, and already
the thing the timeline is scrubbing.

**The proxy is 406x720 for portrait phone source.** Measured on three real
clips — see the proxy-recipe entry under *Open issues* in
[`../reports/prompt-02.md`](../reports/prompt-02.md), and the measurements in
[`../reports/prompt-02-real-footage-check.md`](../reports/prompt-02-real-footage-check.md).
The recipe caps height at 720, and on portrait footage that cap lands on the
long side, so the short side ends up at roughly 406px. Sampling from it sends
Gemini a thumbnail of a 2160x3840 frame.

## Why it would not be caught

**Nothing errors.** A 406x720 JPEG is a valid image, the API accepts it, and the
scene labels come back well-formed, plausible, and validated by the Pydantic
schema. They are simply worse — small detail is gone, and the failure is a
quality regression with no signal attached to it. There is no exception to
catch, no malformed response to handle, and no log line that would look wrong.

`.claude/rules/ffmpeg.md` already says proxies are preview-only and that later
prompts process originals. That rule is correct and it is not enough on its own:
it states the principle, and this file states the specific place the principle
is about to be tested by convenience.

## What Prompt 03's gate must assert

**The sampled frame's dimensions equal the source's display dimensions**, read
from `media_blobs.display_width` / `display_height` — not the proxy's, and not
the source's *coded* dimensions, which are landscape for rotated phone video.

Assert the dimensions of the frame that was actually sampled, not the path it
was read from. A path assertion passes the moment someone adds a resize.

## Two related findings from the same measurement

- **The source is HDR.** The real clips are HEVC Main 10, BT.2020 primaries,
  HLG transfer, with a Dolby Vision RPU. A frame extracted from them without
  tone mapping is washed out and desaturated whichever file it came from, so
  "sample from the source" is necessary but not sufficient — the extraction
  needs a colour conversion the proxy recipe does not currently perform either.
  See *Finding 2* in the real-footage report.
- **Strip the metadata.** `gemini-usage.md` requires EXIF/GPS stripped before
  upload. These clips carry timed-metadata tracks and ambient-viewing-environment
  side data; a frame extracted with `-map_metadata 0` would carry more than the
  picture.
