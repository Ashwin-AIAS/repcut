---
name: gpu-inference-4gb
description: Running RIFE, YOLO-pose and faster-whisper on a 4GB RTX 3050 — model lifecycle, tiling, fp16, OOM recovery, CPU fallback. Use for any GPU inference code or CUDA out-of-memory debugging.
---

# GPU inference on 4GB

Target: **RTX 3050 Laptop, 4GB total, ~3.2GB usable** after driver and
compositor. This constraint shapes the architecture, not just the tuning.

## Single residency — the core rule

RIFE + YOLO-pose + faster-whisper will not fit together. One `ModelManager`
owns loading:

```python
class ModelManager:
    def __init__(self) -> None:
        self._current: str | None = None
        self._model: torch.nn.Module | None = None

    def load(self, name: str) -> torch.nn.Module:
        if self._current == name:
            return self._model
        self.unload()
        self._model = _FACTORY[name]()
        self._current = name
        return self._model

    def unload(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            self._current = None
            torch.cuda.empty_cache()
```

No module-level model globals — they are the most common cause of a model
staying resident past its use.

## Per-model budget

| Model | Approx VRAM | Mitigation |
|---|---|---|
| RIFE (fp16, 1080p) | ~1.5–2.5GB | tile or downscale for 4K |
| YOLOv8n-pose (fp16) | ~0.6–1.0GB | small model is enough for reframing |
| faster-whisper small | ~1.0–1.5GB | `int8_float16`; fall back to CPU freely |

Measure on the actual machine; these are starting points, not facts.

## Tiling

Anything resolution-dependent must tile or downscale. Tile with **overlap**
(~32px) and blend seams, or the joins are visible in motion.

For RIFE specifically, downscale-infer-upscale is often better than tiling —
interpolation is motion-driven and tile boundaries fight the motion field.

## OOM handling — never let it reach the user

```python
try:
    out = model(x)
except torch.cuda.OutOfMemoryError:
    # Named: fragmentation or an oversized frame. Free, halve, retry once,
    # then CPU. A crash here is a user-visible failure of a core feature.
    torch.cuda.empty_cache()
    out = run_tiled(model, x, scale=0.5)
```

## CPU fallback is mandatory

Every GPU path has a CPU path. The engine boots and functions with no GPU at
all; `/health` reports which is active. This is what stops the project becoming
"works only on Ashwin's laptop" — and it is what rots first, so test it.

## Measurement

```python
torch.cuda.reset_peak_memory_stats()
...
peak_gb = torch.cuda.max_memory_allocated() / 1024**3
```
Log peak for every step. Anything scaling with clip length is a bug. Prompt 12
asserts peak stays under budget as a quality gate.

## P1 boundary on RIFE

Interpolation uses real captured frames only. **Hard cap 2x.** Compute an
artifact-confidence score (e.g. optical-flow magnitude and occlusion ratio) and
actually fall back to normal speed when it is low. Interpolation without a
working fallback is a P1 violation.

## CI reality

No GPU on GitHub runners. Mark tests `@pytest.mark.gpu`; CI runs
`-m "not gpu"`. Consequence: this code is only ever tested locally. Run
`make test-gpu` before gating any prompt that touched it, and say so in the
report.
