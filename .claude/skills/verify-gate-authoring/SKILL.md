---
name: verify-gate-authoring
description: How to write a make verify-NN gate that is binary, exit-coded, per-criterion and idempotent. Use when authoring or fixing a prompt's verification script, or turning prose success criteria into executable checks.
---

# Authoring `make verify-NN`

Every prompt's real specification is its gate. Prose success criteria without an
executable script are wishes — and the self-correction loop that makes
autonomous execution safe only works if the gate is real.

## Properties of a valid gate

- **Binary** — PASS/FAIL per criterion, no judgement
- **Exit-coded** — `exit 1` if anything failed
- **Per-criterion** — one line per success criterion from the prompt, not one
  aggregate verdict
- **Idempotent** — re-runnable; cleans up what it creates
- **Self-contained** — no manual setup, no "then check by eye"
- **Fast** — a 10-minute gate is a gate nobody runs

## Method

1. Copy the prompt's Success Criteria verbatim into the script as comments,
   one function per criterion, named after it.
2. For each, ask: *what observable fact proves this?*
3. Print the **measured value**, not just the verdict. The number is what makes
   a failure debuggable.

## Turning vague criteria into measurable ones

| Prose criterion | Executable check |
|---|---|
| "UI shows CUDA true" | GET `/health`, assert `cuda_available is True`, `"3050" in gpu_name` |
| "the grade looks right" | mean ΔE2000 to target statistics below threshold |
| "cuts feel synced" | every cut timestamp within 40ms of nearest beat |
| "export works" | ffprobe output: duration ±0.1s, expected dims, both streams present |
| "no watermark" | pixel-diff corners against a clean render |
| "runs on 4GB" | peak `max_memory_allocated` under 3.2GB |
| "handles VFR" | run against a VFR fixture, assert drift < 40ms at end of clip |
| "job is resumable" | kill mid-job, restart, assert completion and no duplicate rows |
| "override re-syncs" | change track via API, assert every cut timestamp moved |

## Genuine taste is a human gate — say so

If a criterion cannot be scripted, do not invent a proxy that always passes.
The script asserts the **comparison artifacts were produced**, prints
`[HUMAN] <criterion> — artifacts at docs/reviews/prompt-NN/`, and exits 1 until
Ashwin records a verdict. A criterion that cannot fail is worse than no
criterion.

## Output shape

```
verify-05
  [PASS] beat grid extracted for all library tracks    (12/12)
  [PASS] cuts within 40ms of grid                      (max 22ms)
  [FAIL] track change re-syncs cuts                    (3/12 stale)
  [PASS] ducking >= 9dB under speech                   (11.4dB)
  [HUMAN] auto-edit feels right                        (docs/reviews/prompt-05/)
FAILED: 1 of 5 criteria (1 awaiting human)
```

## Never

- Lower a threshold to reach green — fix the code, or run `/guide-amend` with
  evidence that the threshold itself was wrong
- `skip`/`xfail` a failing test to pass a gate
- Mark a taste criterion PASS automatically
- Depend on the user's real footage — use synthetic fixtures
- Leave GPU assertions unmarked; CI has no GPU
