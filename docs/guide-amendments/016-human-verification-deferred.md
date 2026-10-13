# Amendment 016 — human verification is deferred until every prompt is built
Date: 2026-10-10
Affects: every `make verify-NN` from Prompt 04 on; the human stops at Prompts
04, 05, 06, 08 and 10 (STOP A and STOP B at Prompt 04); `/run-prompt`, `/gate`,
`/taste-review`; CLAUDE.md (Autonomy protocol); `testing.md`; `git-and-ci.md`
Status: ACCEPTED

Decided by Ashwin on 2026-10-10. This records the decision.

## What the guide says

Prompts 04, 05, 06, 08 and 10 end at a human taste stop, and the next prompt
does not start until the human signs it. CLAUDE.md's autonomy protocol lists
"HUMAN REVIEW checkpoints" among the only reasons to stop. `/run-prompt` waits
for plan approval, and starts only after the previous prompt is gated, which
`/gate` defines as `make verify-NN` green, every criterion PASS, and the human
verdict recorded. Amendment 012 adds STOP A inside Prompt 04: no grading code
until the colour baseline is signed and the tone-map operator named.

## What we found

The build has stood at STOP A since 2026-09-27. STOP A needs fresh HDR clips
shot with HDR-off twins; the tripod for those shots is lost, and Ashwin's
weekly hours (thesis, part-time job) cannot absorb five sequential taste stops
plus the real-machine gate runs between them. Sequential stops therefore
serialise the whole build behind the scarcest resource, his time and his
equipment, while the machine-checkable work waits.

## Why the current rule doesn't work

It is correct and it does not move. A human stop between prompts is the
cheapest place to catch a taste error, but only if the human can be there; when
he cannot, every later prompt waits, and nothing is learned meanwhile.
Batching the stops costs rework risk (a later prompt built on an unsigned
choice), which is made visible below instead of hidden.

## Proposed change

1. **Defer, don't drop.** Every `[HUMAN]` criterion, STOP A, STOP B, every
   later taste stop, and every real-machine gate run (strict mode, real
   console, GPU) are deferred until Prompts 04–13 are built. They are then
   done in one ordered pass, `docs/manual-checks/VERIFY-LATER.md`.
2. **Every gate is split.** `make verify-NN-auto` runs everything except the
   `[HUMAN]` criteria and must pass before the next prompt starts; it may SKIP
   only under amendment 014. `make verify-NN` is unchanged: it includes every
   `[HUMAN]` criterion and stays red until Ashwin signs. An unsigned box is a
   FAIL in `verify-NN`, and in `-auto` it is printed as `[HUMAN] pending`,
   counted apart, never PASS and never SKIP. The mode is the gate script's own
   argument (`--auto`), not an environment variable, so `make verify-NN` is
   always the full gate; `scripts/gate_human.sh` is the one place both modes are
   decided, and `engine/tests/test_gate_human.py` holds the contract.
3. **The tone-map operator stays `hable`, PROVISIONAL.** Marked so in the code
   comment beside the constant and in every report. A different pick at STOP A
   costs the four-version bump (proxy, scene, frame, Gemini prompt) and a
   re-tune of every theme; VERIFY-LATER.md item 1 says so.
4. **Nobody but Ashwin ticks a box** in `docs/manual-checks/`. The build still
   produces every artifact a stop asks for (contact sheets, samples, edits,
   exports) from footage already on the machine, so the deferred pass has
   something to judge.
5. **Plan approval is pre-granted.** `/run-prompt` writes its plan into the
   report instead of waiting. Branch names follow amendment 015.
6. **A prompt is "BUILD COMPLETE (verification pending)"** when its
   `verify-NN-auto` passes; it is never "done", tagged or merged until
   `verify-NN` passes in strict mode on the gate machine. Prompts stack: each
   branch starts from the previous prompt's branch tip, not from `main`.
7. **Every report carries a "Built on unverified" section** listing which
   pending checks its design assumes, so the deferred pass knows what a failed
   check invalidates downstream.

## Consequences

- Rework risk moves to the end and is bounded by the "Built on unverified"
  sections: a failed check at item N affects the stages its row lists.
- `main` receives nothing until the deferred pass; the stacked branches carry
  the work, pushed to origin.
- Strict mode, a real console and the GPU are still required to gate. Any
  criterion a sandboxed shell cannot run is run individually where possible
  and the report says exactly which ran and which did not.
- Verify-04 criterion 1's error bounds belong to the operator, which is a
  STOP A decision; how `-auto` treats them is amendment 017 (PROPOSED), not
  part of this decision.

## Unchanged

P1–P5. The wording of every `[HUMAN]` criterion. "Never PASS what wasn't
observed": no automated check, no agent and no summary may mark a human
criterion PASS. Amendment 014's skip policy. Never weaken a criterion; a
criterion found wrong gets a proposed amendment.

## Principle check

None of P1–P5 is touched. P2 and P3 are served later rather than less: the
overrides Ashwin makes in the deferred pass are recorded as taste data exactly
as they would have been at each stop.
