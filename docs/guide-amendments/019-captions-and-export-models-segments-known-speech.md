# Amendment 019 — captions and the export: where the model comes from, what a segment is, what "known audio" is
Date: 2026-10-13
Affects: Prompt 06 (Deliverables 1, 2, 4; Constraints; Success Criteria 3 and 5), `scripts/fetch_models.py`
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). Transcription is local, with a small
multilingual model on the GPU and word timings. The master render is one
filtergraph where it can be, with intermediate high-quality segments where it
cannot, resumable per segment, with progress. Caption accuracy is measured on a
known-audio clip; a music-only section must produce no captions.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | The model has to come from somewhere, and Hugging Face was unreachable from both build shells (proxy allowlist) | `fetch_models.py` fetches faster-whisper small from the faster-whisper authors' repository, once, on Ashwin's machine. No digest could be pinned in code yet: each file is checked against the sha256 Hugging Face publishes for it (`X-Linked-ETag`), then recorded in `PINNED.json` beside it and checked on every later run. **Follow-up:** after the first fetch, the printed digests and repository commit go into `captions/transcribe.py` as constants |
| 2 | GPU | CUDA first (int8 weights, float16 compute, ~0.6 GB of 4 GB), CPU int8 when CUDA or its libraries are missing; which ran is logged. No torch (amendment 003 is unaffected: CTranslate2 is not torch) |
| 3 | Intermediate segments | One segment per shot, H.264 crf 18 + 16-bit PCM in Matroska, not ProRes. The final mux **stream-copies** them, so the picture is encoded once in total; ProRes would be encoded twice (ProRes, then H.264) at ~10x the disk. PCM keeps AAC priming gaps out of every cut |
| 4 | Captions and resumability | Captions are burned per segment (each segment gets the lines inside it, shifted), so a caption edit re-renders only the shots it touches and a crash never loses captioned work |
| 5 | "Known audio" | Sentences we wrote (English and German, one mixed clip too), spoken at gate time by Piper (GPL-3.0, a dev-only dependency the engine never imports) with voices trained on public-domain (LJSpeech) and CC0 (Thorsten) speech, fetched by `fetch_models.py --gate`. Nothing is committed. The bound stays 15 % |
| 6 | The guard with no speech detector | Kept exactly as written (low confidence AND no speech overlap). When the speech model is not installed, overlap cannot be shown, so only confidence keeps a line |
| 7 | Render budget | Becomes gate criterion 6 (60 s of 1080p, graded with a LUT and captioned, under 5 minutes), measured on the gate machine |
| 8 | Metadata | Every export strips source metadata (`-map_metadata -1`): phone clips carry location |

## Principle check

P1: nothing is generated in the picture; the crop never pads. P2: style, words,
lines, preset and framing are overridable defaults, and word edits survive a
re-cut. P3: one taste event per change. P4: transcription and rendering are
local; the review page holds no absolute path. P5: every dependency is free and
open source (faster-whisper MIT, CTranslate2 MIT, Whisper weights MIT, Piper
GPL-3.0 dev-only, AGPL-compatible).
