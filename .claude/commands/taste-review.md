---
description: Prepare a human taste checkpoint (prompts 04, 05, 06, 08, 10) and stop
argument-hint: <NN>  e.g. /taste-review 04
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, Agent
---

Prepare the human review gate for prompt **$1**.

Delegate to `taste-gate-prep`. Your job is to make the judgement fast and
well-informed — **not** to form it. Never state that the output looks good.

## Produce
`docs/reviews/prompt-$1/index.html` (local; gitignore anything embedding media)
containing:

1. **Side-by-side comparisons** at matched timestamps, covering the range and
   not the best case: bright gym, dim gym, mixed lighting, mirrors, motion
   blur, portrait phone, handheld shake.
2. **Weakest results first.** Lead with the clips that scored worst. Showing
   only the wins wastes the gate.
3. **Measured numbers alongside** — deltas, sync error, confidence scores, VRAM
   peaks. Context for the eye, not a replacement for it.
4. **Every AI decision, one line each**, so an override carries information.
5. **The specific question for this gate:**
   - **04** — does the grade hold across lighting changes *within one session*,
     or does it fall apart on the dim clips?
   - **05** — do the cuts land where *you* would have cut, or merely where the
     beat is?
   - **06 (Wave 1 gate)** — would you post this without opening another editor?
   - **08** — does it read as the same *look*, or just the same hue?
   - **10** — does it feel like *your* style, or an average of everyone's?

## Then stop
Do not merge. Do not proceed to the next prompt. Wait for Ashwin's verdict.

When he responds, record his verdict **and every override he makes** into
`docs/reports/prompt-$1.md`. Overrides at taste gates are the highest-signal
data the style profile (P3) will ever get — capture the specifics, not a summary.

Metrics passing is not a reason to skip this gate. The numbers can be green
while the output is lifeless; that is exactly why the gate exists.
