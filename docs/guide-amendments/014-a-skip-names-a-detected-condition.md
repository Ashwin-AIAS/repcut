# Amendment 014 — a gate criterion skips only for a condition the gate detects
Date: 2026-09-28
Affects: every `make verify-NN` from Prompt 02 on; `/gate`; amendment 004 §3
(Prompt 02 criterion 13); `testing.md`'s "binary" property
Status: PROPOSED

Decided by Ashwin on 2026-09-28. This records the decision and supersedes
amendment 004 §3, which is not edited.

## What the guide says

`testing.md`: a gate must be "**Binary** — PASS or FAIL, no judgement calls".
`git-and-ci.md`: merge only through `/gate NN`, which requires "`make
verify-NN` green, every success criterion PASS".

Amendment 004 §3: the 2GB memory criterion is "skipped when free disk < 5GB or
`REPCUT_SLOW=0` … The gate runs it and reports `SKIPPED` **with the reason**
rather than passing silently."

## What we found

The prompt-04 gate-script audit (session report, 2026-09-28) found that a gate
exits 0 when criteria SKIP (`PASSED: 5 of 5 criteria (3 skipped)`), and that a
SKIP was whatever a check said it was:

- A skip reason was free text. Of nine skip sites, three matched one of the
  conditions below: no console (twice), and no guide, which verify-02 #22 also
  used when the guide was present but yielded no titles. The other six were a
  missed runtime budget, a git tag absent from a clone (twice), two "debt not
  landed yet" branches, and verify-02 #13's `REPCUT_SLOW=0`: an environment
  variable anyone can set to make a criterion vanish.
- Until the same audit, exit 2 with no reason also counted as SKIP. Python
  exits 2 when it cannot open a script, so a missing checks module read as
  "skipped" and the gate passed.
- Nested gates (04 → 03 → 02 → 01 → 00) printed only their summary line, so a
  skip two levels down never appeared on screen at all.

The proposed fix, an `ALLOW_SKIP=16` variable, was rejected: an allow-list
anyone can set is a loophole, and it would miss verify-02 criterion 22's
legitimate skip where the guide is absent (amendment 006) and the GPU skips
still to come.

## Why the current rule doesn't work

"Report the skip with its reason" makes a skip visible, not justified. A gate
that passes with free-text skips certifies criteria that never executed, and
`/gate` inherits that exit code.

## Proposed change

1. **A criterion may SKIP only for an enumerated environment condition:**
   `NO_CONSOLE`, `NO_GUIDE` or `NO_GPU`. The check prints
   `SKIPPED: <CONDITION> <reason>`, and the gate asks
   `scripts/gate_conditions.py` independently whether that condition holds
   here. A skip for any other reason, for no reason, or for a condition that
   does not hold is a **FAIL**. The skip line prints the condition and the
   gate's own evidence for it. The detector has no override variable.
2. **Strict mode, zero skips:** with `REPCUT_GATE_STRICT=1`, every skip is a
   FAIL. `/gate NN` always runs strict. It runs on Ashwin's machine, which has
   a console, the guide and a GPU, so every criterion must actually execute
   there. The variable can only make a gate harder to pass, and nested gates
   inherit it.
3. **Nested gates surface their skips:** each gate prints every `[SKIP]` line
   of the gate it runs, so a skip at any depth is visible at the top.
4. **One choke point:** every skip in `scripts/verify_*.sh` goes through
   `gate_skip` in `scripts/gate_skip.sh`; a test fails if a gate records one
   directly.

Supersedes amendment 004 §3's skip: criterion 13's reasons (`REPCUT_SLOW=0`,
under 5GB free, no ffmpeg) are none of the three conditions, so the criterion
now runs or FAILs.

## Consequences

- verify-03: the runtime-budget miss, the missing `prompt-02-done` tag and the
  two "not landed yet" branches now FAIL. The budget miss follows `testing.md`
  (fix the code or amend the ratio, never excuse it). verify-04 criterion 4's
  missing-tag skip likewise.
- A shallow clone or a low disk now fails a gate instead of passing it. That is
  intended, and the message names the cause.
- Criterion 16 (Ctrl-C) SKIPs as NO_CONSOLE where there is no console, and
  fails under `/gate`, which runs from a real terminal.
- Future GPU criteria use `NO_GPU`; CI never runs the verify gates.
- Tests: `engine/tests/test_gate_skips.py`. An unknown reason fails; a
  detected condition skips in normal mode and fails in strict mode; a claimed
  condition the detector finds false fails.

## Principle check

None of P1–P5 is touched. This tightens the process that enforces them.
