# Rule: FFmpeg

## Construction
- **Every** FFmpeg invocation is built by `engine/media/ffmpeg_builder.py`.
  Never concatenate command strings. Never pass `shell=True`. Arguments are a
  `list[str]`, always.
- Filter graphs are composed programmatically and unit-tested as strings before
  any file is touched.
- Every builder call logs the full argv at DEBUG (safe — no secrets in FFmpeg
  commands, but never log absolute paths containing the username).

## Correctness traps that must be handled explicitly
- **VFR phone footage.** Recordings from phones are variable frame rate. Beat
  syncing, cut timing, and interpolation all silently drift on VFR input.
  Normalize on ingest (`-vsync cfr` / `fps` filter) and record both the source
  and normalized frame rate in the DB. Never assume the container's nominal fps.
- **Rotation metadata.** Portrait phone video is often landscape pixels plus a
  rotate side-data tag. Read and apply it; never trust raw dimensions.
- **Audio drift.** Concatenating segments with different sample rates desyncs
  audio. Resample to a single project rate on ingest.
- **Timebase.** Use `-copyts` deliberately or not at all; mixing timebases
  across the cut pipeline produces off-by-frames errors that only show up in
  the final export.
- **Colour range/matrix.** Preserve or explicitly set `bt709` + range on every
  encode, or grades shift between preview and export.

## Encoding
- Rendering uses `libx264` unless a prompt says otherwise. NVENC may be used
  for previews only — quality-critical exports go through x264.
- Exports are watermark-free. Presets live in config, not in code.
- Always two-pass the *plan* (dry-run the filter graph on a 2-second slice)
  before rendering the full timeline. Fail fast.

## Failure handling
- Parse FFmpeg's stderr for known failure classes; raise typed exceptions
  (`FFmpegEncodeError`, `FFmpegFilterGraphError`, `UnsupportedCodecError`).
  Never surface a raw stderr dump to the UI.
- Every render is resumable: write to a temp path, atomically move on success.
