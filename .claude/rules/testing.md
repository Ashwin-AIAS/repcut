# Rule: Testing & quality gates

## The core contract
Every prompt's real specification is `make verify-NN`. Prose success criteria
without an executable script are wishes. A gate must be:
- **Binary** — PASS or FAIL, no judgement calls
- **Exit-coded** — `exit 1` on any failure
- **Per-criterion** — prints PASS/FAIL for each of the prompt's success
  criteria individually, not one aggregate result
- **Idempotent** — safe to run repeatedly, cleans up after itself

## Two modes per gate (amendment 016)
- `make verify-NN` is the gate: every criterion, `[HUMAN]` included. An
  unticked `[HUMAN]` box is a FAIL — never PASS, never SKIP.
- `make verify-NN-auto` (`verify_NN.sh --auto`) runs everything a machine can
  check. An unticked `[HUMAN]` box prints as `[HUMAN] pending`, counted apart.
  It must pass before the next prompt starts; it may SKIP only under
  amendment 014. Green here says nothing about `verify-NN`.
- Every `[HUMAN]` criterion goes through `gate_human` (`scripts/gate_human.sh`),
  the gate ends with `gate_finish`, and a gate nesting another passes
  `$(gate_mode_arg)`. The mode is the script's argument, never an environment
  variable. `engine/tests/test_gate_human.py` enforces all of it, including
  that every `docs/manual-checks/prompt-NN.md` is read by its gate.
- Only Ashwin ticks a box in `docs/manual-checks/`.

## Test layers
| Layer | Tool | Scope |
|---|---|---|
| Engine unit | pytest | pure functions, builders, planners, parsers |
| Engine integration | pytest + async client | API routes, job lifecycle, DB |
| UI unit | vitest | hooks, state reducers, formatting |
| E2E | Playwright | upload → analyze → edit → export happy path |
| Quality gates | pytest + scripts | measurable product claims |

## Fixtures
- **Never commit media.** Generate synthetic clips at test time:
  `ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 -t 3 …`, with a
  `conftest.py` factory producing clips of chosen fps/duration/VFR-ness.
- Include a deliberately **VFR** fixture — it catches the most common real bug.
- Gemini is always mocked in tests. Zero live API calls in the suite, ever.

## Markers
- `@pytest.mark.gpu` — requires CUDA. Excluded in CI (`-m "not gpu"`), run
  locally with `make test-gpu`.
- `@pytest.mark.slow` — full renders. Excluded from the fast loop.

## Quality gates (Prompt 12 formalizes; earlier prompts contribute)
Every measurable product claim gets a script that fails the build:
- Beat sync: cut points within ±40ms of the detected beat grid
- Slow-mo: artifact confidence threshold enforced; fallback actually triggers
- Captions: word error rate below threshold on a fixed reference clip
- VRAM: peak allocation under budget on every GPU step
- Export: output is playable, correct duration, correct dimensions, no watermark
- Determinism: same input + same seed = same edit plan

## Never
Do not mark a failing test `skip` or `xfail` to reach green. Do not lower a
threshold to pass. Fix the code or, if the threshold was genuinely wrong, run
`/guide-amend` and record why.

A test simulating "a future value of X" derives it from X (e.g. `X + 1`),
never hardcodes a literal — a hardcoded literal is a landmine that detonates
silently the day X's real value reaches it.
