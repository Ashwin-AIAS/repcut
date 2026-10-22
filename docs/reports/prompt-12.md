# Prompt 12 — the quality harness: tests, the eval, the standing suites
PUSH PENDING: run `git push -u origin feat/quality-harness` from Git Bash (and `feat/look-transfer`, `feat/first-seconds`, `feat/taste-profile`, `feat/talk-to-timeline`, still pending)
Branch: feat/quality-harness (stacked on feat/talk-to-timeline @ 4fc1570) · Built 2026-10-22 in one scheduled run · Real-machine runs deferred (amendment 016)

**Tone-map operator: hable, PROVISIONAL (STOP A unsigned).** The eval's frame gates measure the engine's own proxy, so they inherit it.

## Plan (pre-approved under amendment 016)
Pin the linters; coverage floors; `eval_metrics.py` (pure verdict maths) +
`eval_pipeline.py` (one gate per claim) + `eval_fixtures.py` (local real corpus,
hash-only manifest) + `eval_report.py` (scorecard); beat mutation check; security
ledger + missing regression tests; orphan collection (owed since amendment 004);
browser happy path over CDP; seeded test order; `verify_12.sh`; Makefile, CI.
Built inline (one shared path; no specialist split was worth the handoff).

## Built
Commits: ea34843 (pins, mypy), 7f95881 (eval, fixtures, scorecard), fb168c2
(security ledger, coverage floors), 0edea4c (GC, browser E2E), 76ba55b (gate,
make, CI, test order), plus this report, amendment 025, VERIFY-LATER item 11,
`testing.md` and the changelog.
- `make test` = engine suite + 80% floors (edit 93.5%, grading 93.7%, analysis
  92.2%, measured from the whole suite today) + vitest + browser happy path.
- `make eval`: wave-1 time and beat sync on one real render, caption WER, slow-
  motion fallback, reps within one, skin hue across all 5 themes, reframing;
  `gemini-live` only with `REPCUT_GEMINI_LIVE=1`. Today: **7 BOUND** (no labels
  yet), exit 3 - the committed `eval-latest.md` says so. That is the first scorecard.
- Mutation: clean generated render 5/5 cuts within 40 ms (worst 16 ms); same
  render with music +100 ms: 0/5, lag measured +104 ms -> FAIL, as required.
- Security: ledger of the 2026-08-07 review's 10 findings -> 17 tests; 8 new
  (rebinding Host, upload ceiling, limit -1/0/1e5, audits, next major, headers,
  bandit scope, ENGINE_URL scheme). `make gc`: orphans found, removed, zero left.

## Decisions made autonomously (amendment 025, PROPOSED)
Real fixtures stay local, manifest hash-only; BOUND is never PASS (exit 3,
strict 1); beats read from the render's own sound, offset searched under half
a beat; mutation on generated clips by design; skin = LUT-only, verify-04's
4 deg; reframe gate added for the walking fixture; CDP instead of Playwright;
CI time read via gh, no hard timeout; seeded shuffle; GC scope (superseded
versions kept); linters pinned.

## Gate (verify-12-auto), run criterion by criterion (shell limit 180 s)
1a: the whole suite could not run as one process here; every one of the 60
files ran shuffled (seed 20261022, files in reverse order): all green; coverage
from today's whole-suite per-file runs: floors met. **Whole-suite shuffle not
seen here.** · 1b vitest 259/259, tsc 0, eslint 0 (Linux copy of ui/): PASS ·
1c browser: NOT RUN (no Chrome, no `make dev` here) · 2 eval: PENDING (bound,
exit 3, scorecard written) · 2b fixtures: PENDING (0 labelled; ignored by git;
manifest test PASS) · 2c 12 passed: PASS · 3 mutation: PASS (92 s) · 3b CI
shape 6/6: PASS · 3c CI time: PENDING (no gh here) · 4 security 56 + 2 UI
files: PASS · 5 Gemini mocked: PASS · 6 GC: PASS · 7 lint pinned, ruff/format/
mypy clean (208 files): PASS · 8 verify-11 not re-run as a whole (its code is
unchanged; its tests are in 1a) · 9 plan leak clean (524 files): PASS.
Commit guard: every commit through `scripts/hooks/pre-commit`; refusals: 0.

## Built on unverified
- Every eval gate past BOUND: none has measured real footage yet; the slow-
  motion, reps, reframe and caption gates call model code this shell cannot load.
- Beat gate on real footage: cut location by frame difference is proven on
  generated clips only; fast real motion near a cut may need a wider margin.
- Items 5-9 (models, labels) and STOP A (hable) feed these numbers.
- CI duration with coverage on: never run; may exceed 10 min.

## Open questions for the human
- Amendment 025: accept? Is the reframe gate (added) a claim you want gated?
- The live Gemini gate tried once from the build shell and could not reach
  the API (transport error, no request completed); it now runs only on your flag.

### [HUMAN] criteria
None in this prompt. Real-machine: VERIFY-LATER item 11 (labels, `make eval`, the strict gate, CI time).

BUILD COMPLETE (verification pending)
