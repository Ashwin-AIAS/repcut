---
description: Structured debugging for FFmpeg, CUDA/VRAM, VFR footage, and Gemini quota failures
argument-hint: [optional error text or file]
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent
---

Debug: $ARGUMENTS

Work the loop — reproduce, isolate, diagnose, fix, prove. Do not guess-and-patch.

## 1. Classify first
Repcut failures cluster into five families. Identify which before touching code:

| Symptom | Likely family | Go to |
|---|---|---|
| Encode fails, filter graph error, wrong dimensions/duration | **FFmpeg** | `video-pipeline-engineer` |
| Sync drifts, worse toward the end of the clip | **VFR timing** | see below — most common real bug |
| `CUDA out of memory`, process killed, slow-then-crash | **VRAM** | `/vram`, `gpu-model-engineer` |
| 429, quota exhausted, empty/garbage analysis | **Gemini** | `gemini-steward` |
| Job stuck, no progress events, half-written output | **Engine/jobs** | `engine-architect` |

## 2. Reproduce minimally
Use a **synthetic clip**, not the user's footage (faster, and P4):
```
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 -t 3 /tmp/t.mp4
```
For a VFR repro, build a variable-rate fixture — many bugs only appear there.

## 3. The VFR check — run this early, it explains a surprising share of bugs
```
ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate,avg_frame_rate \
  -show_entries format=duration -of default=nw=1 <file>
```
If `r_frame_rate != avg_frame_rate`, the source is variable frame rate. Any
timing computed from the nominal fps is wrong, and the error compounds over the
clip — which is why it looks fine at the start and drifts at the end.

## 4. VRAM check
Log `torch.cuda.max_memory_allocated()` around the failing step. Budget is
~3.2GB usable on a 4GB 3050. Ask: is more than one model resident? Is a tensor
scaling with input resolution or clip length? Is `empty_cache()` called after
unload?

## 5. Isolate
Bisect: filter graph stage by stage, pipeline step by step, `git bisect` across
commits if it used to work.

## 6. Fix, then prove
Add a regression test that fails before the fix and passes after. A bug fixed
without a test will return. Note the fix in the session report — including bugs
found in earlier prompts' code (fixing those is inside your autonomy).
