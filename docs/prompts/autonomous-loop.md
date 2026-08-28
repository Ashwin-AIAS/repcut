# Autonomous gate loop — paste-ready

A self-correcting loop for Claude Code: run the gate, fix the lowest-numbered
failure, re-run, repeat until green or until a real stop condition is hit.

**What this is not.** It is not a way to turn a red gate green. A gate is the
prompt's specification (`.claude/rules/testing.md`), so the only legitimate way
out of a red run is code that satisfies it. Every shortcut that makes a gate
green without satisfying it is enumerated below as forbidden, because a loop
that is allowed to weaken its own success condition converges on nothing.

**Human criteria are outside the loop.** Any criterion marked `[HUMAN]` — 16 in
`verify-02`, and the taste checkpoints at Prompts 04, 05, 06, 08, 10 — cannot be
satisfied by an agent. It cannot see the footage, hear the desync or judge the
grade. The loop must treat those as a stop, never as a target.

If this proves useful, it belongs in `.claude/commands/loop.md` as `/loop NN`.

---

## PROMPT — autonomous loop

```
Work autonomously in a loop until `make verify-NN` passes every criterion that
is not marked [HUMAN], or until you hit a stop condition below. Do not ask me
between iterations. Report once, at the end.

### The loop

1. Run `make verify-NN`. Record the per-criterion result.
2. If every non-[HUMAN] criterion passes, stop — you are done.
3. Otherwise take the LOWEST-numbered failing criterion. One at a time, in
   order: later criteria often fail as a consequence of earlier ones, and
   fixing three at once hides which change did what.
4. Diagnose before you touch code. State what you believe the cause is and
   what observation supports it. If you cannot name an observation, you are
   guessing — go measure instead.
5. Make the smallest change that addresses that cause.
6. Re-run the gate. If the criterion still fails, the diagnosis was wrong:
   revert the change and return to step 4 with what you learned. Do not stack
   speculative fixes on top of each other.
7. Repeat.

### Forbidden — every one of these ends the loop as a FAILURE

- Marking a test `skip` or `xfail` to reach green.
- Lowering a threshold, widening a tolerance, or relaxing an assertion.
- Deleting, weakening or commenting out any criterion.
- Adding an ignore entry — gitleaks, ruff `noqa`, mypy `type: ignore`,
  `eslint-disable`, `npm audit` or `pip-audit` — to silence a finding.
- `git commit --no-verify`, or any push to `main`.
- Ticking a box in `docs/manual-checks/`. Those are the human's signature. An
  agent ticking one is forging a sign-off on evidence it has not seen.
- Changing the gate so it measures something easier than the criterion states.

If a criterion looks genuinely wrong rather than unmet, that is not a fix you
make silently: stop, and propose a `/guide-amend` with the reasoning.

### Stop and report — these are not failures, they are my decisions

- Every non-[HUMAN] criterion passes.
- A [HUMAN] criterion is the only thing failing. Say so and stop.
- The same criterion has failed three iterations running with no new
  information. Report the three attempts and what each ruled out — three
  attempts that eliminated three hypotheses is progress worth handing over,
  three attempts that repeated one guess is a loop that needs a person.
- A P1–P5 conflict, anything paid, a credential, or a destructive action
  outside the repo.
- A fix would require changing an approved-stack dependency, a schema that
  another prompt already depends on, or a rule in `.claude/rules/`.
- 10 iterations, whichever comes first. A budget you report against beats an
  agent that runs all night on one wrong assumption.

### Discipline while looping

- Commit at each green criterion, not at the end. A loop that produces one
  enormous commit is unreviewable, and a revert loses the good work with the
  bad.
- Keep per-iteration output to a few lines: criterion, hypothesis, change,
  result. The long write-up goes in the session report at the end.
- Run negative controls on anything you claim to have fixed. A test that
  passes after your change and would also have passed before it has proved
  nothing.
- Never assume a measurement. If you report a number, you ran the thing that
  produced it in this session.

### At the end

One report: which criteria were failing at the start, what each cause turned
out to be, what changed, and the final per-criterion result. Anything you
worked around rather than through goes in `docs/reports/prompt-NN.md` under
Open issues, explicitly — a loop that ends green with an unrecorded workaround
is worse than one that ends red.
```
