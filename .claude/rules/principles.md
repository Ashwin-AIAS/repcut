# Rule: Design principles P1–P5 (invariants)

A prompt that violates one of these has FAILED, even if every other success
criterion passes. When a task conflicts with a principle: stop and ask.

## P1 — Natural only
Repcut enhances what the camera captured. It never generates or replaces
content.

- **Allowed:** color grading, stabilization, denoise, audio cleanup, cutting,
  reframing/cropping, captions overlaid on top, speed changes.
- **Gray zone, tightly bounded:** RIFE frame interpolation for slow motion.
  Interpolates only FROM real captured frames. Hard cap **2x**. Must compute an
  artifact-confidence score and fall back to normal speed when confidence is
  low. Never ship interpolation without the fallback path.
- **Banned forever:** generative fill, background replacement, sky
  replacement, body or face alteration (slimming, smoothing, beautify),
  object insertion or removal, AI-generated b-roll, face swap, voice cloning.

Check before shipping any visual feature: *could a viewer be shown something
that did not happen in front of the camera?* If yes, it violates P1.

## P2 — AI recommends, user decides
Every AI output is a **default**, never a lock. Theme, music track, cut points,
slow-mo moments, caption text and style, crop framing, transitions — all
overridable in the UI.

Overrides must **re-sync dependents**. Changing the song re-snaps cuts to the
new beat grid. Changing scene order re-times captions. An override that leaves
the edit internally inconsistent is a bug.

## P3 — Overrides are taste signals
Every override is logged with enough context to learn from (what was
recommended, what was chosen, scene features). Feeds the style profile in
Prompt 10.

## P4 — Privacy & honesty
- Footage never leaves the machine. Only **sampled frames — one per scene** go
  to the Gemini API. Never a full video, never an audio track, never a batch of
  frames "just in case."
- This is disclosed in the UI at the point it happens.
- No dark patterns. No "AI magic" labelling on features that are not AI.
- Never log or transmit file paths containing the user's name.

## P5 — €0
Free tiers, open source, local GPU. If a deliverable needs a paid service, an
account signup with a card, or leaving the approved stack: **stop and ask.**
Do not silently substitute a paid API or a trial.
