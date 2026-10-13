---
description: Run make verify-NN and report PASS/FAIL per success criterion
argument-hint: <NN>  e.g. /verify 05
allowed-tools: Read, Grep, Glob, Bash, Agent
---

Run the gate for prompt **$1**.

1. Re-read PROMPT $1's Success Criteria from the build guide (`$REPCUT_GUIDE_PATH`)
   plus any amendments in `docs/guide-amendments/`.

2. Run `make verify-$1`.

3. Report **one line per success criterion** with the measured value, not just
   a verdict:

```
verify-$1
  [PASS] <criterion>  (measured: <value>)
  [FAIL] <criterion>  (measured: <value>, required: <threshold>)
  [SKIP] <criterion>  (<CONDITION>: <the gate's evidence>)
FAILED: N of M criteria
```

   A SKIP is only ever `NO_CONSOLE`, `NO_GUIDE` or `NO_GPU`, confirmed by the
   gate itself (amendment 014). List every SKIP, including those printed from
   nested gates. `/verify` runs normal mode; `/gate` runs strict, where a skip
   fails.

4. If the prompt touched GPU code, also run `make test-gpu` locally and report
   it — CI cannot run those tests.

5. On failure: diagnose and fix the code. **Never** lower a threshold, skip a
   test, or add an xfail to reach green. If a threshold is genuinely wrong, run
   `/guide-amend` and record the reasoning.

6. If a criterion is genuine taste rather than measurement, do not fake-pass it
   — confirm the comparison artifacts exist and route to `/taste-review $1`.

If `make verify-$1` does not exist yet, use `gate-runner` to author it from the
prompt's success criteria first.
