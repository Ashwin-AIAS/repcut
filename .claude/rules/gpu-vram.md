# Rule: GPU & VRAM — 4GB budget

Target hardware: **RTX 3050 Laptop, 4GB VRAM.** This is the binding constraint
of the entire project. Code that only works on a bigger card is broken code.

## Budget discipline
- Assume **~3.2GB usable** (driver + desktop compositor take the rest).
- Only ONE model resident at a time. Load → infer → `del` → `torch.cuda.empty_cache()`.
  Never hold RIFE, YOLO-pose and Whisper simultaneously.
- Use a single `ModelManager` with explicit load/unload; no module-level model
  globals.
- fp16 by default for RIFE and YOLO. Verify output quality, don't assume.

## Required patterns
- **Tile or chunk** anything frame-resolution dependent. 4K input at full res
  will OOM RIFE. Downscale-infer-upscale or tile with overlap.
- **Batch size 1** unless measured otherwise. Never let batch size scale with
  input length.
- **CPU fallback is mandatory, not optional.** Every GPU path has a CPU path.
  `/health` reports which is active. The engine must boot and function on a
  machine with no GPU at all.
- Catch `torch.cuda.OutOfMemoryError` explicitly → free, halve the tile/batch,
  retry once, then fall back to CPU with a logged warning. Never let an OOM
  reach the user as a crash.

## Measurement, not guessing
- Every GPU step logs peak allocated VRAM (`torch.cuda.max_memory_allocated`).
- `/vram` command profiles a step and proposes a fix.
- Prompt 12 asserts peak VRAM stays under budget as a quality gate.

## CI
GPU code **never runs in CI** — GitHub runners have no GPU. Mark all such tests
`@pytest.mark.gpu`; CI runs `-m "not gpu"`. The consequence is real: GPU paths
are only ever exercised locally, so `make test-gpu` must be run before every
`/gate` on a prompt that touched GPU code.
