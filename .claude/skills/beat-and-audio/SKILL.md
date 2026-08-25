---
name: beat-and-audio
description: Beat detection, cut-to-beat snapping, silence removal, and voice ducking for Repcut. Use for Prompt 05 and any timing, sync, or audio-mix work, or when an edit drifts out of sync.
---

# Beat, timing & audio for Repcut

Timing errors are the most visible failure in a finished edit. A viewer who
can't name what's wrong will still feel it.

## Beat grid extraction (librosa)

```python
y, sr = librosa.load(path, sr=22050, mono=True)
tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
onset_env = librosa.onset.onset_strength(y=y, sr=sr)
```

Persist per track, once, at import: BPM, beat times, downbeat estimates, an
energy envelope, and duration. Re-analyzing on every edit is wasted time.

**Downbeats matter more than beats.** Cutting on every beat at 128 BPM means a
cut every 470ms — exhausting to watch. Cut on downbeats and phrase boundaries
(commonly every 4 or 8 bars); use off-beats only for deliberate accents.

## Tempo drift

Live-recorded or tempo-varying tracks have a drifting grid. `beat_track`
returns a single tempo and the error accumulates. Either use the returned beat
*times* directly (not `tempo × n`), or detect variance and reject the track
with a clear message. Silently syncing to a wrong grid is the worst outcome.

## The VFR trap — read this before debugging any sync bug

Phone footage is variable frame rate. If cut timing is computed from the
container's nominal fps, error accumulates across the clip: **fine at the
start, visibly off at the end.** That symptom means VFR until proven otherwise.

All beat math runs against **normalized CFR timing** from ingest. Coordinate
with `video-pipeline-engineer`.

## Cut planning

- Candidate cut points = scene boundaries ∩ near-downbeats
- Snap tolerance: **±40ms**. That is the quality gate.
- Enforce a minimum shot length (~0.6s) — below that it reads as a glitch
- Match cut density to section energy: sparse in the intro, dense in the peak

## Store cuts as musical positions, not timestamps

```python
CutPoint(bar=8, beat=1, offset_ms=0)   # not: t=14.812
```

This is the P2 requirement and it must be designed in from the start.
Retrofitting it is painful. Changing the track then becomes a recompute against
the new grid, and every cut re-snaps automatically.

## Silence removal

```
silencedetect=noise=-35dB:d=0.4
```
Tune the threshold to gym noise floors (clanking plates, air conditioning —
higher than a quiet room). Always keep a small pad (~120ms) around speech;
tight cuts on breath sound amateurish.

## Ducking (silero-vad)

Detect speech regions, then attenuate music beneath them. Target **≥9dB**
reduction with ~150ms attack and ~400ms release — abrupt ducking is audible as
a pump. Alternative: `sidechaincompress` in FFmpeg, but VAD-driven gives
cleaner control over exactly where it engages.

## Licensing — flag, never decide

Music is user-supplied in `$DATA_DIR/music/`, outside the repo. Never commit audio. Never
fetch from an unverified source. Keep the local attribution ledger. Unclear
license → stop and tell Ashwin. "Export without music" stays a first-class path.
