# Wave 1 walkthrough — from raw clips to a postable file

Branch: feat/render-out · Written 2026-10-13 · Human checks deferred (amendment 016)

This is the whole loop as it stands at the end of the first wave, stage by
stage, with what each stage reads and writes and how long it took. Every stage
runs on this machine; the only thing that ever leaves it is one sampled frame
per scene to Gemini, and only with a key set (P4).

| # | Stage | What happens | Reads → writes |
|---|---|---|---|
| 1 | Upload | Chunked, resumable, hashed; one blob per content | browser → `$DATA_DIR/media` |
| 2 | Ingest | Probe (rotation, VFR, colour tags), 720p CFR proxy normalised to bt709 SDR (HDR tone-mapped, operator PROVISIONAL), thumbnail strip | source → proxy |
| 3 | Analysis | Scenes, motion and audio energy, one sampled frame per scene to Gemini (cached) | proxy → `scenes`, tags |
| 4 | Grading | Per-scene correction measured on the proxy, theme LUT by intensity, grain | proxy → `grade_choices` |
| 5 | Music + footage | Beats, bars, sections, energy per track; motion, shake, loudness and speech per clip | tracks, proxies → caches |
| 6 | The edit | Usable segments, a beat-snapped cut list, the drop on the best footage, ducking under speech | caches → `edit_plans` |
| 7 | Captions | Local speech-to-text (faster-whisper small, en + de per segment), the music guard, words onto the timeline, the person's edits by word id | proxy audio → transcript cache |
| 8 | Export | Per shot from the ORIGINAL file: CFR, the same normalisation, crop + scale to the preset, the same grade chain, captions on top; joined by stream copy, ducked music mixed, AAC 48 kHz, faststart, metadata stripped, then checked on the file | sources → `projects/<id>/export/repcut-<preset>.mp4` |

## Timings measured so far

On the build shell (a 2-core Linux VM, no GPU; the ROG will be faster), with
captions off because the speech model could not be downloaded there:

| Run | Stages |
|---|---|
| Three generated clips (1080p landscape, 1080x1920 portrait, VFR 720p), 6 s Reel | fixtures 5.1 s · upload + ingest + analysis 18.9 s · grade + music + edit 10.7 s · export 21.1 s |
| Two real gym clips (1024x576), 29.7 s Moody Reel, 7 shots | music + footage signals 13.9 s · edit 2.6 s · export 75.9 s (shots 75.1 s, mux 0.7 s, check 0.06 s) |

Not yet measured: transcription time, and the guide's budget (60 s of 1080p in
under 5 minutes on the ROG), which is `verify-06` criterion 6.

## What the person controls

Every AI value is a default with a visible "AI suggested" state and a reset:
theme, intensity and light per scene; track, length, locks and removed cuts;
caption style, every word, every line, captions on or off; the format and a
per-scene crop nudge; music on or off. Each change is one taste event.

## Known limits at this point

- The centre crop cuts a lifter at the frame edge (seen in the scratch review);
  the per-scene nudge is the v1 answer, subject tracking comes later.
- A short out-of-focus moment made it into the scratch review edit: the footage
  scorer reads motion and shake, not focus.
- HDR footage is tone-mapped with a PROVISIONAL operator until STOP A.
