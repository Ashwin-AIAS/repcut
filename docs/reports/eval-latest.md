# Eval scorecard (latest run)

Run: 2026-10-22 22:42 UTC on Linux x86_64 at commit `ea34843`. Written by `scripts/eval_report.py` from `make eval`; regenerate, never hand-edit.

**0 pass, 0 fail, 7 bound.** BOUND means the real-footage fixture, its label or a model is not on the machine that ran it: not measured, and never a pass. Synthetic clips never stand in for a claim.

| Gate | Claim | Metric | Measured | Bound | Verdict | Note |
|---|---|---|---|---|---|---|
| wave1-time | 3 real clips to an exported reel in under 15 minutes | session to export | not measured | < 900 s | BOUND (not measured) | session fixtures: 0 of 3 spans labelled |
| beat-accuracy | 100% of cuts within 40 ms of the beat, in the rendered file | cuts on the beat | not measured | 100% | BOUND (not measured) | session fixtures: 0 of 3 spans labelled |
| caption-wer | captions under 15% word error rate on labelled real speech | word error rate | not measured | < 15% | BOUND (not measured) | known-audio fixture: 0 of 1 spans labelled |
| slowmo-revert | slow motion falls back to normal speed on every artifact-prone span | revert rate | not measured | 100% | BOUND (not measured) | barbell fixtures: 0 of 1 spans labelled |
| rep-count | rep count within one of the human count | reps within one | not measured | 3 of 3 | BOUND (not measured) | rep fixtures: 0 of 3 spans labelled |
| skin-tone | every theme keeps skin hue within 4 degrees on real footage | worst skin hue drift | not measured | <= 4.0 deg | BOUND (not measured) | skin labels: 0 of 2 spans labelled |
| reframe | a walking subject stays in the middle third of a 9:16 crop | frames in the middle third | not measured | >= 95% | BOUND (not measured) | walking fixture: 0 of 1 spans labelled |

How to fill a BOUND row: `docs/manual-checks/VERIFY-LATER.md`, item 11.
