# Amendment 021 — a reference's look is measured against a baseline; what each part becomes
Date: 2026-10-15
Affects: Prompt 08 (Deliverables 1-5; Constraints; Success Criteria)
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). Take a reference clip the person
provides; extract its colour profile as a reusable theme, its pacing as
planner parameters and its caption style; apply all three in one click, each
detachable; statistical colour mapping only, under the grading caps; reuse the
scene pipeline; never fabricate cuts; prove it with a round trip through a
built-in theme, two references of different pace, saved themes that survive a
restart, hostile-input handling, and a human resemblance check.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | "Extract the look from the result" has no reference point: a frame's statistics alone cannot say how much of its contrast is the camera's and how much the grade's | The look is fitted **relative to a baseline**: what, applied to the baseline, reproduces the reference's colour statistics. The cached profile uses a generated neutral baseline (`look.neutral_baseline`); matching or saving a look inside a project refits against that project's own footage, which is what the theme will be applied to. The round trip uses clip A's ungraded frames as the baseline: grade A with Moody through the real FFmpeg chain, extract, compare |
| 2 | Which numbers are fitted | A two-stage least-squares fit with `lut.py`'s own functions as the forward model: tone (contrast, black lift, black crush, highlight roll-off) to the luma quantile transfer curve, then saturation, warmth and two split tones to per-luma-band chroma means and spread. Pivot is held at the default (pivot and lift/crush describe the same curves); skin protection is never fitted (kept at the default); grain is not extracted (finish is a finish, not the look) |
| 3 | "Bounded by the same safety rails" | Every parameter is fitted inside `Look`'s own bounds; one the reference wanted past a cap is held there and named (`capped`), logged, shown in the panel and the review page. The LUT generator, gamut compression and skin protection are the same code as every theme |
| 4 | "A Repcut Theme (data file)" saved forever | One TOML file per saved look under `$DATA_DIR/grading/saved/`, validated by `load_theme` like a pack file, ids `ref-<slug>` (cannot shadow a built-in), joined to the gym pack after its own themes (`grading/saved.py`). Never committed (user data) |
| 5 | Pacing profile -> "planner parameters" | The theme's `PaceSpec` (energy from cuts per 10 s; calm and peak shot lengths in beats of the reference's own tempo, or of the pacing tempo without a beat) plus one new field, `slowmo_every_seconds`, which replaces Prompt 07's fixed one-slow-shot-per-15-s allowance when a reference is matched (default unchanged: 15 s) |
| 6 | "Slow-mo usage estimate" | Share of consecutive near-identical frames in a one-second window per scene: catches frame-doubled or conformed slow motion, misses high-frame-rate capture played slowed. Labelled an estimate; it only moves the allowance (8 s / 15 s / 30 s) |
| 7 | Caption classification "via Gemini" | Local first: a text-band detector on each scene's sampled frame gives presence, position and type height with no key. With a key, at most 4 of those frames get one Gemini question each (own prompt and schema, `gemini_client.ask_about_frame`, same limiter, EXIF strip and key header), which may override position and size and adds letter case and an animation guess. Answers are kept in the per-blob profile, so a reference is never asked twice |
| 8 | "Closest caption style + overrides" | Caption settings hold a style id only, so the override part is reported, not applied: the panel lists what still differs (e.g. a top position has no Repcut style; centre is nearest). A reference without captions turns captions off. Position/size overrides are a later prompt's |
| 9 | Reference ingest | `media_files.role` (`footage` / `reference`, migration 0007). A reference is uploaded through the same upload routes (same probe, ceiling, refusals: security §7) and marked; it runs the same ingest and analysis jobs plus a `reference` job; it is never cut into the edit. No route accepts a URL; the panel says the person must have the right to use the reference |
| 10 | "Reports the gap, never fabricates cuts" | With pacing matched, the edit adds one warning when its median shot is more than 1.25x the reference's. The planner itself is unchanged: with too little footage it still reuses real footage with its existing warning; it never invents a shot |
| 11 | Each part detachable | `reference_matches` records what each applied part replaced; detaching puts back the person's own choice (only where the match's value is still in place) and writes a `reference_detached` taste event; matching writes `reference_matched`. CORS now allows `DELETE` for that one route |
| 12 | Gate in this build shell | verify-08 adds criteria for security, P1 (no model in `reference/`), match/detach/UI and the migration, and nests verify-07. Here the UI half of criterion 7 runs only in a Linux mirror of `ui/` (the repo's `node_modules` is the Windows install) and the nested verify-07 cannot finish (no torch); both are real-machine runs deferred by amendment 016 |

## Principle check

P1: statistical colour mapping through the existing LUT generator, inside the
caps, skin hue protected; no model, no texture, no content from the reference.
P2: three independent parts, each a default that detaches and restores. P3:
every match and detach is a taste event with the reference's profile as
features. P4: measured locally; only the reference's per-scene sampled frames
(at most four, for captions) go to Gemini, as for any clip. P5: numpy/scipy,
already installed with librosa; no service.
