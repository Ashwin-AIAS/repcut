# Amendment 018 — the auto-edit: where music and the speech model live, and what "remove" removes
Date: 2026-10-12
Affects: Prompt 05 (Deliverables 1, 3, 7, 8; Success Criteria 3 and 6), Prompt 06 (inherits the mux and the music stem)
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). The music library is watched in the
repo's data folder; voice activity uses silero-vad, named and local; the edit
review screen lets a person remove or lock each cut; ducking is measured in
rendered audio; the final stop renders one 30 s edit from the person's real
session with the top-recommended track.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | The repo's `data/` folder | Amendment 004 already moved user data to `$DATA_DIR`, outside the repo and outside cloud sync. The library is `$DATA_DIR/music/`; a README with the licence rule is written there on first scan |
| 2 | silero-vad's pip package requires torch; amendment 003 defers torch to Prompt 07 | The same published model (silero-vad v5.1.2 ONNX, MIT) through onnxruntime (MIT) on the CPU, with the package's own chunking. The model is not committed: `scripts/fetch_models.py` fetches the pinned release once and checks its sha256. The engine never downloads |
| 3 | "Remove a cut" | Removes that shot's **source range**, not its whole take. Removing the take also hid footage a locked shot came from, which broke "locked cuts survive" (found by the API test) |
| 4 | "Rendered audio" for the ducking criterion | The ducked music stem rendered by FFmpeg and measured against the same window undocked. Voice + music mixing belongs to the export (Prompt 06); the review edit already mixes them (`build_edit_mux`) |
| 5 | The final stop's track | Repcut never downloads music. If the library has no analysed track, the review generates one, at the theme's tempo, into `$DATA_DIR/music/` under a name that says it was generated |
| 6 | "Theme energy profile" | A `[pace]` table in each theme file (energy, calm and peak shot length in beats). Themes stay data; the LUT digest reads `[look]` only, so no LUT changes |

## Principle check

P1: nothing is generated in the picture; the generated track is audio for a
review, labelled as such. P2: every edit value is an overridable default; locks
survive re-plans by construction. P3: one taste event per override. P4: audio
and frames stay local; speech detection runs on the CPU. P5: every dependency
is free and open source (librosa ISC, onnxruntime MIT, the model MIT).
