# Amendment 003 — torch deferred to Prompt 07; Prompt 01 proves the CPU path
Date: 2026-08-03
Affects: Prompt 01 (Success Criteria), Prompt 07 (Success Criteria — inherits)
Status: ACCEPTED

## What the guide says

Prompt 01, Success Criteria:

> UI status page shows CUDA true, GPU name RTX 3050

Read literally, this makes a working PyTorch CUDA install a Prompt 01 gating
requirement: `cuda_available` must be `true` and `gpu_name` must be a real
device string before Prompt 01 can close.

## What we found

Nothing in Prompt 01 — or in the five prompts after it — imports torch.

| Consumer | Prompt | Uses torch? |
|---|---|---|
| Engine scaffold, `/health` | 01 | No — `probes.py` imports it defensively, reports `cpu` when absent |
| Ingest / FFmpeg normalization | 02–03 | No |
| Colour grading (LUTs) | 04 | No — FFmpeg filter graphs |
| Beats / audio | 05 | No — librosa, silero-vad |
| Captions | 06 | No — faster-whisper is CTranslate2, not torch |
| **RIFE slow-mo** | **07** | **Yes — first real consumer** |
| YOLO-pose | 10 | Yes |

The three places Prompt 01 touches torch all already treat it as optional:

- `engine/repcut/probes.py` imports it via `importlib.import_module` inside a
  `try`, and returns the CPU answer on `ImportError`.
- `scripts/check_env.py` marks every GPU row `hard=False` — WARN, never FAIL.
- `scripts/verify_01.sh` criterion 5 boots the engine with torch blocked from
  import and asserts `/health` still returns 200 with `device=cpu`.

A previous install attempt disconnected partway through the ~2.3GB CUDA wheel
download. pip does not resume partial downloads, so the retry restarts from
zero — the cost of installing torch now is real and repeatable.

## Why the guide's version doesn't work

The criterion gates Prompt 01 on a dependency that Prompt 01 does not use, that
CI can never satisfy (GitHub runners have no GPU), and that no code will import
for six more prompts. Three concrete problems:

1. **It gates on the wrong path.** The device Prompt 01 must prove correct is
   `cpu` — that is the path CI exercises on every push, the path any non-GPU
   contributor runs, and the mandatory fallback that `gpu-vram.md` requires
   every GPU step to have. `cuda_available: true` cannot be asserted by any
   automated gate this project runs.
2. **It cannot be a binary criterion.** Per `.claude/rules/testing.md`, a gate
   must be binary and machine-checkable. "Shows CUDA true" is machine-checkable
   only on one specific laptop, so it is not a gate — it is an observation.
3. **Six prompts of version drift.** Installing a CUDA wheel now pins a
   torch/CUDA pair against which nothing is written until Prompt 07. RIFE has
   its own version constraints; discovering a conflict at Prompt 07 against a
   six-prompt-old install is strictly worse than installing to fit RIFE then.

## Proposed change

**Amend Prompt 01, Success Criteria** — replace:

> ~~UI status page shows CUDA true, GPU name RTX 3050~~

with:

> - `/health` returns 200 and `torch_device_active` correctly reports `cpu`
>   when torch is absent, and `cuda` when it is present. The status page
>   renders whichever device is active, plus `gpu_name` and VRAM when there is
>   a GPU.

**The deleted criterion is DEFERRED, not dropped.** Add to Prompt 07 (RIFE),
Success Criteria:

> - Inherited from Prompt 01 (amendment 003): with the CUDA build of torch
>   installed, `/health` reports `cuda_available: true`, `gpu_name` naming the
>   physical device, and non-null `vram_free_mb` / `vram_total_mb`; the UI
>   status page renders all four.

Prompt 07 is the correct owner: it is the first prompt that cannot function
without torch, so it is the first prompt whose gate can honestly require it.

**Add to Prompt 01, Constraints:**

> - Do not install `torch`, `torchvision`, or `torchaudio`. They arrive with
>   Prompt 07.

## Consequences

- `scripts/verify_01.sh` requires no criterion to have torch present. Criterion
  4 asserts the ten `/health` fields and their types without constraining
  `cuda_available`; criterion 5 asserts the CPU answer with torch blocked from
  import. No change to the script was needed — this amendment records that its
  existing shape is deliberate.
- `scripts/check_env.py` keeps WARNing that torch is absent, and now names
  Prompt 07 as when it will be needed and `make setup-gpu` as how. A WARN that
  explains itself, not a silent pass.
- `make setup-gpu` already exists and is unchanged — the install path is ready
  for Prompt 07, it is simply not run yet.
- No already-passed gate is invalidated. `verify_01.sh` passes 12/12 on a
  machine with no torch, which is the point.
- The RTX 3050 / 4GB VRAM budget in `.claude/rules/gpu-vram.md` is unaffected;
  it is a design constraint, not a Prompt 01 assertion.

## Principle check

**P5 (€0)** — positively. torch is free, but the ~2.3GB download is a real cost
in time and bandwidth on a metered connection, paid six prompts before use.

**P1, P2, P3** — untouched. No change to what the editor generates, to
overridability, or to taste logging.

**P4** — untouched. The privacy boundary is unchanged; no probe sends anything
off the machine, and `/health` still exposes no path and no credential value.

Relevant to `.claude/rules/gpu-vram.md`: this amendment *strengthens* the
mandatory-CPU-fallback rule by making the CPU path the one Prompt 01's gate
actually binds on, rather than an untested branch behind a GPU-only criterion.
