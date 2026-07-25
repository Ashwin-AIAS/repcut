---
description: Profile GPU memory for a step and propose a fix within the 4GB budget
argument-hint: [step or file to profile]
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent
---

Profile VRAM for: $ARGUMENTS

Budget: **RTX 3050 Laptop, 4GB total, ~3.2GB usable.**

## Measure
1. `nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv`
   before starting — know what the desktop already consumes.
2. Wrap the step:
```python
torch.cuda.reset_peak_memory_stats()
... run step ...
peak = torch.cuda.max_memory_allocated() / 1024**3
```
3. Report peak per step, and whether peak scales with input resolution, clip
   length, or batch size. Anything that scales with clip length is a bug.

## Diagnose in this order
1. **More than one model resident?** RIFE + YOLO + Whisper together will not
   fit. Enforce single-residency through `ModelManager`.
2. **Missing unload?** `del model` then `torch.cuda.empty_cache()` after use.
   Module-level model globals are a common cause.
3. **Full-resolution inference?** 4K into RIFE will OOM. Tile with overlap, or
   downscale-infer-upscale.
4. **fp32 where fp16 works?** Halves activation memory. Verify output quality.
5. **Batch > 1?** Default to 1 unless measured.
6. **Fragmentation?** Try `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

## Deliver
- Peak VRAM before and after the fix, as numbers
- The fix applied, with the tradeoff stated (speed cost of tiling, quality cost
  of fp16)
- Confirmation the **CPU fallback path still works** — it is mandatory, and it
  is the thing that most often silently rots
- A test asserting peak stays under budget, marked `@pytest.mark.gpu`
