# Amendment 024 — the copilot's registry, one undo stack, and what its eval can prove here
Date: 2026-10-21
Affects: Prompt 11 (Deliverables 1-6; Constraints; Success Criteria), `make verify-11`, `make verify-11-auto`
Status: PROPOSED

## What the guide says

Summarised, not quoted (amendment 006). A chat panel whose words become the
same edit operations the controls call, through function calling over a typed,
validated registry that is the only way in; multi-step requests run in order;
an ambiguous request gets one question; natural-only violations are refused by
name; unsupported ones are logged; every copilot change shares the controls'
undo and is a taste event with its source; offline leaves the controls alone;
a compact plan summary; conversation local. Prove it with a 20-intent eval,
byte-identical undo for every operation, button/chat equivalence, an offline
run and a prompt-injection fixture.

## What we found, and what this build did

| # | Collision | Resolution |
|---|---|---|
| 1 | Model name | Gemini 3.5 Flash, pinned (amendment 010), through the shared client, key header and limiter. Request body built by the copilot; `gemini_client.generate_content` is the shared transport |
| 2 | "One behaviour, two front doors" | `copilot/tools.py`: 15 operations, strict pydantic arguments (`extra="forbid"`, bounds, id patterns), then a check against the current plan. The edit, grading, caption and export routes call them. Changes with no copilot operation (a scene's grade, a hidden caption line, a reframe nudge, a grade reset, a scene boundary) still run through `tools.recorded` so they share the stack; `set_scene_boundary` is in the registry but not offered to the copilot |
| 3 | What "byte-identical plan state" is judged on | The project's decision rows (edit plan, grading choices, caption and export settings) as canonical JSON. The edit's `plan_json` is derived (re-planned on read), so it is restored with its decisions but not compared when deciding whether a change happened. The test compares after the change and after its undo |
| 4 | Undo semantics | Selective: an entry reverts only rows that still hold its "after" value; anything changed since is a 409 with a sentence. The undone change's taste events are deleted (an undone change was not a choice). Redo is not built |
| 5 | Speech acts | Three non-mutating functions beside the registry: ask one question, refuse naming a principle, report unsupported. A natural-only guard (`copilot/principles.py`) runs before any request is sent; the model is the second line. "Background music/noise" is sound and passes |
| 6 | Prompt injection | A call to a name outside the allowlist ends the turn, **undoes every change made earlier in that turn**, and is logged (`copilot_unmet_requests`, kind `blocked_call`). Frame-derived text in the summary is reduced to 24 letters/digits and marked `[data]` |
| 7 | The 20-intent eval without a live model | The fixture (`engine/tests/fixtures/copilot_intents.json`, our own phrasings) carries each intent's expected outcome and call sequence plus a scripted model answer. The gate replays it (criterion 1: it proves the turn loop, validation, refusals, the unmet log and the judging). **Whether the real model reaches >= 90% is criterion 1L**: `REPCUT_COPILOT_LIVE=1 make verify-11` on the laptop, ~30 free-tier requests; pending in `-auto` and a FAIL in the gate until run (amendment 017's pattern) |
| 8 | "Full UI editing still green in E2E" | There is no browser E2E suite yet (the quality-harness stage builds one). Criterion 4 is the engine's offline test plus every panel's UI tests; the browser run joins it there |
| 9 | Pacing as taste (amendment 023 #3) | Not added: "shorter" through the copilot logs the same target-duration event as the control. A pacing event from chat is left for the profile's next revision |
| 10 | Dev tools drift | ruff and mypy are unpinned in the dev extras; a fresh environment picked up rules that flag earlier tests. ruff was fixed here; under mypy 2.4, seven type errors in earlier test files remain (none in `engine/repcut`). Proposed: pin both in the quality-harness stage and fix those tests there |
| 11 | Gate in this build shell | As in 021 #12 / 023 #14: the full gate is killed by the 180 s shell limit, so its criteria were run one by one, the UI ones in a Linux copy of `ui/`, and the nested verify-10 was not re-run as a whole (its criteria's tests are in the engine and UI suites, both green) |

## Principle check

P1: the guard and the model refuse generation, replacement and body edits;
slow motion stays inside the existing 2x cap. P2: the copilot only calls the
operations a person could press; every change is a card with undo. P3: copilot
changes write the same taste events, `source=copilot`. P4: the request is the
words plus a text summary, no frames, no names, no paths; the conversation is
in local SQLite. P5: free tier through the shared limiter; with no key or
budget the panel is off and nothing else changes.

## Consequences

`verify-11-auto` can pass while `verify-11` stays red on 1L until the live run.
Accepting this amendment accepts that a replayed eval is evidence about Repcut,
not about the model.
