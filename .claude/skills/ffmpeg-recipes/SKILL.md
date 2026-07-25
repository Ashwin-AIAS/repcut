---
name: ffmpeg-recipes
description: Verified FFmpeg patterns for Repcut — ingest normalization, VFR handling, rotation, filter graphs, concat, encode presets, probing. Use whenever building or debugging an FFmpeg command, or when output timing, colour, or dimensions are wrong.
---

# FFmpeg recipes for Repcut

All commands are built as `list[str]` by `engine/media/ffmpeg_builder.py`.
Never string-concatenate. Never `shell=True`. These are the argv patterns the
builder emits.

## Probe before you touch anything

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt,color_space \
  -show_entries stream_side_data=rotation \
  -show_entries format=duration,bit_rate \
  -of json input.mp4
```

**Read `r_frame_rate` vs `avg_frame_rate`.** If they differ, the source is
variable frame rate. This is the default for phone footage and the root cause
of most sync bugs — they look fine at the start and drift toward the end.

## Ingest normalization (run on every upload)

```
ffmpeg -i in.mp4
  -vf "scale=-2:1080:flags=lanczos,fps=30"
  -c:v libx264 -preset medium -crf 18
  -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709 -color_trc bt709
  -c:a aac -ar 48000 -ac 2
  -movflags +faststart
  norm.mp4
```

Why each part:
- `fps=30` → forces CFR. Everything downstream (beats, cuts, interpolation)
  assumes constant frame timing.
- `-ar 48000` → one project sample rate. Mixed rates desync on concat.
- explicit `bt709` → preview and export agree on colour. Without this, grades
  shift between the two and the bug is nearly invisible.
- `+faststart` → the browser player can seek before the whole file loads.

Store **both** source fps and normalized fps in the DB.

## Rotation

Portrait phone video is often landscape pixels + a rotation tag. Modern ffmpeg
auto-applies it on decode, but always verify output dimensions after ingest
rather than trusting the input's reported width/height.

## Trim precisely (re-encode; stream copy cuts to keyframes only)

```
ffmpeg -ss 12.500 -to 18.250 -i in.mp4 -c:v libx264 -crf 18 -c:a aac out.mp4
```

`-ss` before `-i` is fast but keyframe-snapped; after `-i` is frame-accurate
but slower. For cut planning, accuracy wins — the beat gate is ±40ms.

## Concat with re-encode (safe across differing sources)

```
ffmpeg -f concat -safe 0 -i list.txt -c:v libx264 -crf 18 -c:a aac -ar 48000 out.mp4
```

Demuxer concat with `-c copy` only works when every input shares codec,
resolution, and timebase. After normalization it usually does — but verify,
don't assume.

## Filter graph composition

```
-filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,
                 crop=1080:1920,lut3d='theme.cube'[v];
                 [1:a]volume=0.6[m];[0:a][m]amix=inputs=2:duration=first[a]"
-map "[v]" -map "[a]"
```

Dry-run any new graph on a 2-second slice before rendering the timeline.

## LUT application

```
-vf "lut3d=file='data/luts/gym_warm.cube':interp=tetrahedral"
```

`tetrahedral` interpolation, not the default trilinear — fewer banding
artifacts on the gradients gym lighting produces.

## Encode presets

| Purpose | Settings |
|---|---|
| Preview (speed) | `-c:v h264_nvenc -preset p4 -cq 28` |
| Export (quality) | `-c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p` |
| Vertical 9:16 | `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920` |
| Square 1:1 | `scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080` |

Quality-critical exports go through x264. NVENC is for previews.

## Synthetic test fixtures (never commit media)

```
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 -t 3 -pix_fmt yuv420p t.mp4
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a aac a.m4a
```

Build a VFR fixture too — it catches the bug class that matters most.

## Typed errors

Parse stderr and raise: `FFmpegEncodeError`, `FFmpegFilterGraphError`,
`UnsupportedCodecError`. Never surface raw stderr to the UI.
