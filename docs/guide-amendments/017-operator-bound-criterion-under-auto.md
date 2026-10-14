# Amendment 017 — a measured bound that belongs to a deferred decision is pending under -auto
Date: 2026-10-10
Affects: `make verify-04-auto` criterion 1; `scripts/gate_human.sh`
(`gate_stop_bound`); any later criterion whose bound depends on a human pick
Status: PROPOSED

## What the guide says

Amendment 012: the tone-map operator is a taste decision made at STOP A.
Verify-04 criterion 1 requires the HDR proxy's error against its SDR reference
to be at most 0.30 of the v1 proxy's error and at most 20 codes; the report
records that hable misses both (ratio 0.45, 33.4 codes) and that mobius,
reinhard and clip meet them, and that "the next step is a proposed amendment,
never a changed rule" if the chosen operator fails.

## What we found

Amendment 016 defers STOP A and requires `verify-04-auto` to pass before
Prompt 05 starts. Criterion 1 is automated, so 016 alone keeps it in `-auto`,
where it fails for a reason only STOP A can settle. Neither choice available
without a decision is honest: dropping the criterion from `-auto` hides its
other sub-checks (tags, luma band, the v1 negative control, the SDR proxy),
and switching the operator to one that passes would make STOP A's decision for
Ashwin.

## Proposed change

1. `verify_04_checks.py proxy-colour` keeps every bound exactly as written. It
   exits 3 only when its sole failures are the two operator error bounds, and
   1 for anything else.
2. `gate_stop_bound` in `scripts/gate_human.sh`: exit 0 is PASS; exit 3 is a
   FAIL in `verify-04`, and `[HUMAN] pending` with the measurement printed in
   `verify-04-auto`; every other exit is a FAIL in both modes.
3. VERIFY-LATER.md item 1 (STOP A) carries the consequence: if Ashwin keeps
   hable, criterion 1 still fails and this amendment's follow-up (amend the
   bound with the measurements, or change the operator) is decided then.

## Consequences

- `verify-04-auto` can pass with hable while `verify-04` stays red on criterion
  1 and criterion 8, which is the truth.
- Tests: `test_gate_human.py` checks that exit 3 is pending only in `-auto`,
  and that exits 1, 2 and 130 fail in both modes.

## Principle check

None of P1–P5 is touched. No bound is lowered; the verdict is postponed to the
person whose decision it is.
