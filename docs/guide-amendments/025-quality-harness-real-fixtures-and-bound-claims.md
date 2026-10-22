# Amendment 025 — the quality harness: real fixtures kept local, claims that stay bound until measured
Date: 2026-10-22
Affects: Prompt 12 (Deliverables 1-6; Constraints; Success Criteria), `make test`, `make eval`, `make verify-12`, `make verify-12-auto`; amendment 004 (orphan collection, owed here); `.claude/rules/testing.md` (Fixtures)
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). One `make test` (engine with coverage
floors on the edit, grading and analysis code, UI unit tests, a browser happy
path against a running stack with Gemini mocked); an eval script that gates
each product claim (end-to-end time, beat accuracy on the real render, caption
error rate, slow-motion fallback, rep count, skin tone across themes); a small
versioned corpus of REAL test media, synthetic-only media being banned for the
eval; CI running the unit and UI tests on every PR; a markdown scorecard; every
gate exits 1 naming its metric; Gemini mocked everywhere with one opt-in live
smoke; an order-independent suite. Prove it: everything green, every eval gate
PASS on current main, CI under 10 minutes, a deliberately broken beat-sync
failing the eval, and a regression test for every security finding to date.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | Real media in a "versioned" corpus vs a PUBLIC repo that never commits media (`secrets.md`, `testing.md`) and footage that stays local (P4) | The corpus is cut from the owner's own clips by `scripts/eval_fixtures.py` into `$DATA_DIR/fixtures/` (gitignored, never uploaded). What is versioned is `engine/tests/fixtures/eval_fixtures.json`: per fixture the source clip's sha256 and a span, plus labels (transcript, skin box) - no path, no file name, no pixels. Rep spans reuse `rep_labels.json`. Neither git-lfs nor a download script: both would publish footage |
| 2 | The corpus needs labels only a person can write | The manifest ships empty. Until Ashwin fills it (VERIFY-LATER item 11, ~20 min), every eval gate reports **BOUND**: not measured, never PASS. `make eval` exits 3 on BOUND (1 under `--strict` / `REPCUT_GATE_STRICT=1`); verify-12 criteria 2 and 2b are `gate_stop_bound` (amendment 017): pending under `--auto`, FAIL in the gate. A gate that crashes is a FAIL with the exception named, never a skip |
| 3 | "Beat accuracy re-asserted on the real render" - what is measured | The exported MP4 itself: each planned cut is located in the decoded frames (strongest change within 150 ms); the beats are where the generated track actually lands in the rendered sound (onset cross-correlation against the track), not the plan's numbers. A beat grid repeats, so the offset is searched within half a beat period, where it is unique. A split inside one continuous take shows no cut and is not counted. Bound 40 ms, 100% of cuts |
| 4 | The mutation test | `eval_pipeline.py --harness-check`: the same generated render passes, and with its sound 100 ms late fails (measured today: clean 5/5, worst 16 ms; late 0/5, lag seen +104 ms). Generated clips by design - it tests the gate, not a product claim, so the synthetic ban does not apply |
| 5 | Skin tone "across all themes" - against what | The same definition and bound as verify-04 9a (4 degrees): the theme's LUT at intensity 100 against the ungraded frame, on a labelled skin box in the engine's own SDR proxy of the fixture. Scene correction is excluded: it moves white balance on purpose |
| 6 | Fixtures and colour | Spans are re-encoded frame-accurately, timestamps passed through (a VFR clip stays VFR), 10-bit with the source's colour tags (HDR stays HDR); every frame-level gate measures the engine's own proxy of the fixture, so HDR is tone-mapped exactly as in the product (hable, PROVISIONAL) |
| 7 | The walking-subject fixture has no claim in the list | Added one: the reframe claim (subject centre in the crop's middle third >= 95%, verify-10's bound), measured with the person detector on the real clip |
| 8 | "Playwright" | `scripts/e2e_browser.py` drives installed Chrome/Edge over CDP (`cdp_browser.py`, amendment 012) against `make dev` with no key: pick a theme, lock a cut, the copilot disabled with its reason, export, a Download that passed the platform check. Clips enter through the engine's upload route (a headless file picker needs a second driver); everything after is clicks. Needs the laptop; not run in the build shell |
| 9 | Coverage targets | `engine/repcut/{edit,grading,analysis}`, 80% lines each (`scripts/coverage_floor.py`, in `make test` and CI). Measured 2026-10-22 from the whole suite: 93.5%, 93.7%, 92.2%. CI measures the not-slow subset |
| 10 | CI < 10 min | Read from GitHub with `gh` (criterion 3c); bound until a PR runs. No `timeout-minutes` added: it would cancel a slow run rather than measure it. If the engine job is over budget, `pytest-xdist` (free) is the first lever |
| 11 | Order independence | `REPCUT_TEST_ORDER=reverse` or `shuffle:<seed>` reorders the run (conftest); criterion 1a runs the whole suite shuffled with a dated seed and prints it |
| 12 | The opt-in live Gemini smoke | An eval gate, `gemini-live`, present only with `REPCUT_GEMINI_LIVE=1`: one generated frame, one free-tier request, the scene schema back. Every other gate and child process runs with the key blanked |
| 13 | Orphan collection (amendment 004, and the 2026-08-07 review's follow-up on abandoned uploads) | `repcut.media.gc` + `make gc`: blob and derived folders with no `media_blobs` row, `.part` files with no live session or a session idle 7 days (marked aborted), killed-render partials older than an hour. Referenced bytes and superseded recipe versions stay. Dry run by default. The `stored_path`-is-relative CHECK constraint stays a follow-up for the next migration |
| 14 | "A regression test for every finding to date" | `engine/tests/fixtures/security_findings.json` maps each row of each review's summary table to tests; a test fails if a row or a named test is missing. Findings 1, 4, 5, 6, 7, 9 and 10 had prose but no test; each has one now (rebinding `Host`, upload ceiling, negative limit, audit jobs, `next` major, UI headers, bandit scope, `ENGINE_URL` scheme) |
| 15 | The export-time claim is about the laptop | The scorecard records the machine; only a run on the gate machine counts for the claim |
| 16 | Linters drifted between runs | `ruff==0.17.2`, `mypy==2.4.0` pinned in `engine[dev]`; the seven test-file type errors fixed |

## Consequences

- `make eval` is a local pre-release ritual, never CI (no fixtures, no models
  there). It writes `docs/reports/eval-latest.md`; the committed one today is
  all BOUND, honestly.
- Filling the manifest is part of the deferred pass and takes footage choice,
  which only Ashwin can make: three session clips (one VFR), speech with its
  transcript, two to four barbell spans, a walking clip, two skin frames.
- `.claude/rules/testing.md` "Fixtures" gains one line: eval fixtures are real
  and local; unit and gate fixtures stay generated.
