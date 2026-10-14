# Amendment 020 — slow motion: which model, how confidence is measured, where it goes in the edit
Date: 2026-10-14
Affects: Prompt 07 (Deliverables 1-5; Constraints; Success Criteria), `scripts/fetch_models.py`
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). Score hero moments from motion, sound,
scene context and position in the set; interpolate them 2x with a pinned,
open-source RIFE on the 4 GB card, tiling on OOM; check every interpolated
frame and fall back to normal speed on low confidence; place hero moments on
musical drops with a per-cut toggle; fetch hash-checked weights with a model
script; prove it with a labelled real session, a GPU timing, a barbell clip
and the export's platform checks.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | "RIFE, pinned release" names no release | RIFE **4.6** (Practical-RIFE, MIT): small (21 MB), widely used, the architecture is 100 lines. The network is vendored (`slowmo/ifnet.py`, MIT notices in `docs/licenses/rife.md`); the weights come from the vs-rife project's GitHub `model` release with a sha256 **measured on the downloaded file** when this was written, so it is a pin, not trust-on-first-use. Loaded only through `weights.load_verified` (hash, then `torch.load(weights_only=True)`) |
| 2 | "Model download script `get_models.py`" | Folded into the existing `scripts/fetch_models.py` (amendment 019): one place a model arrives, one digest-checking path. No `get_models.py` exists, so nothing can download weights two ways |
| 3 | "Tile if needed" | RIFE's own `scale` is the tile: the flow network runs at 1.0 / 0.5 / 0.25 of the frame while the warp samples full resolution. 1080p starts at 1.0, larger at 0.5. On `torch.cuda.OutOfMemoryError`: free, halve, retry once, then the CPU (gpu-vram.md). Peak VRAM is reported per shot |
| 4 | "SSIM discontinuity + warp-error heuristic" | Both, on luma at 360 px, inside a motion mask (static pixels interpolate perfectly and would hide a broken bar). **Warp error** is measured against ground truth: interpolate over a frame the camera recorded and compare with it (leave one out; pessimistic, so the safe direction for P1). **Discontinuity**: an invented frame must be at least as similar to each neighbour as the neighbours are to each other. Judged at the shot's worst tenth, not its mean. Threshold **0.80, PROVISIONAL**: generated gentle motion scores 0.97-0.998, the generated barbell pan 0.21-0.22, a mid-speed pan over a pattern 0.61. Not yet seen on real footage (P4 kept it off the build workspace): VERIFY-LATER item 6 |
| 5 | What a refused shot becomes | The planner reserves the full-length normal-speed window around the peak for every slow shot (`Shot.fallback_in`), so a refusal at export never moves a cut, the music or a caption. A shot also plays at normal speed when torch or the weights are missing - same path, reason recorded |
| 6 | The 2x ceiling "no config can exceed" | `slowmo/limits.py`: the only speeds that exist are 1.0 and 0.5; anything else, from any source (a stored plan included), is refused, not clamped. The interpolator has no timestep parameter |
| 7 | Sound under a slow shot | Silence; the music carries it. Half-speed audio is not what was heard. Ducking and captions skip slow shots for the same reason |
| 8 | How many slow shots | One AI-chosen slow shot per 15 s of edit (at least one), 1-4 s long, best hero on the drop's shot when that shot fits; a person's "slow this" is not limited. Locked shots keep their speed; changing it needs an unlock |
| 9 | Criterion 1 needs a human label | `engine/tests/fixtures/hero_labels.json` (digest + seconds, no path); until it has a session, criterion 1 is pending in `--auto` and FAIL in the gate (`gate_stop_bound`, amendment 017). A generated-session check (1a) runs everywhere |
| 10 | Real-machine criteria in this build shell | The laptop shell cannot install torch (disk) and has no GPU; the cloud build workspace has CPU torch and the weights but no Hugging Face (so verify-06, nested as criterion 10, still cannot finish there). Criteria 2b-9 were run in the cloud workspace on generated clips; 2 and 6 SKIP as NO_GPU; 10 is deferred to the gate machine with verify-06 itself |

## Principle check

P1: interpolation only between captured frames, 2x at most by construction,
with the measured fallback; nothing else in the picture changes. P2: every slow
shot is a default with a per-cut toggle; turning one off survives re-plans.
P3: `slowmo_on` / `slowmo_off` are taste events with the hero score and its
signals. P4: no frame leaves the machine for any of this (the build used only
generated clips). P5: RIFE and its weights are MIT; torch is free.
