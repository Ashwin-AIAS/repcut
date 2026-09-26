# Amendment 010 — Gemini 2.0 Flash retired; pinned to Gemini 3.5 Flash
Date: 2026-09-19
Affects: CLAUDE.md stack line, `.claude/rules/gemini-usage.md`, Prompt 03's
Gemini integration (`engine/repcut/analysis/gemini_client.py`,
`engine/repcut/analysis/pipeline.py`)
Status: ACCEPTED

## What the guide/stack said

CLAUDE.md's approved stack line and `.claude/rules/gemini-usage.md` both name
"Gemini 2.0 Flash" as the model. `gemini_client.py` pinned
`GEMINI_MODEL = "gemini-2.0-flash"` accordingly.

## What we found

Four real clips were analysed; every scene came back `vlm: null` while every
job reported `succeeded`. No engine scrollback survived to inspect, so the
cause was confirmed by direct API probe instead:

- `GET /v1beta/models?key=...` → `200`. The configured key is valid — this is
  not the no-key degrade path.
- `POST /v1beta/models/gemini-2.0-flash:generateContent?key=...` → `404`.
- The full model list reachable by this key has no `2.0` series at all.

`gemini_client._post` treats a 404 as `GeminiAPIError(404)` — logged
`gemini_response_error status_code=404` — which `cache._call_with_backoff`
retries three times then gives up, returning `reached_api=False`. Per
`cache.analyze_scene_cached`, that degrades with `source="degraded"` and
writes **no** cache row (the write only happens when `reached_api=True`), so
every one of these jobs called the retired model, exhausted its retries, and
degraded silently and gracefully exactly as `.claude/rules/gemini-usage.md`
requires — the defect is the pinned model having aged out, not a bug in the
retry/cache/degrade logic itself.

## Model choice: Gemini 3.5 Flash, not a lite tier or an alias

Two models were verified reachable on this key's free tier before choosing
between them:

- `POST .../gemini-3.5-flash:generateContent` → `200`, real content returned.
- `POST .../gemini-2.5-flash:generateContent` → `404` ("no longer available to
  new users") — also retired, ruling it out as a fallback.

`gemini-flash-latest` was considered and rejected: an alias moves to whatever
the provider currently calls "flash" with no changelog Repcut would see,
which reintroduces this exact failure mode (or a silent quality regression)
on the provider's schedule instead of a decision this project makes and
records. `GEMINI_MODEL` stays an explicit, pinned string.

Between `gemini-3.5-flash` and a `-lite` variant: `GeminiSceneCache` means
each scene is analysed exactly once per `(scene_id, prompt_version)` ever, so
free-tier RPD headroom is not the binding constraint a lite model would be
optimising for. The binding constraint is tag quality — identifying an
exercise from one dim, motion-blurred sampled frame is hard, Prompt 03's
manual-check box 3 judges exactly that, and every prompt from 04 onward
(color grading, cut pacing, captions, the style profile) reads these tags
downstream. A lite model returning a plausible-but-wrong label is the
expensive failure mode here, because nothing downstream can tell a wrong
label from a right one — it just silently degrades every later prompt's
inputs. Full quality over quota headroom that was never going to bind.

## Verification of the fix

Re-ran the Gemini step for real, through `cache.analyze_scene_cached` (the
same function `pipeline._analyze_with_gemini` calls), against the real
sampled frames already on disk for the four clips, with
`GEMINI_PROMPT_VERSION` bumped to 2 so the (empty, since nothing was ever
cached under the old failure) lookup could not mask a live call. Findings are
in the session's diagnostic report, not retyped here; summarized: real
`generateContent` calls succeeded end to end and produced non-null tags —
see the report for whether `gemini_response_unparseable` appeared and how it
was handled.

## Consequences

- `GEMINI_MODEL` = `"gemini-3.5-flash"`, pinned, in `gemini_client.py`.
- `GEMINI_PROMPT_VERSION` bumped 1 → 2 in `pipeline.py`, in the same commit —
  every cache row written under version 1 (there are none, since the 404
  path never wrote one) or read under it is invalidated regardless.
- CLAUDE.md, `.claude/rules/gemini-usage.md`, the `gemini-free-tier` skill,
  README.md, and the `gemini-steward`/`copilot-engineer` agent descriptions
  are updated to name Gemini 3.5 Flash — the stack line is corrected, not
  silently left to say something no longer true.
- Standing rule, restated here so it outlives this one incident: **a Gemini
  model change always ships with a `GEMINI_PROMPT_VERSION` bump in the same
  commit.** A stale answer from a model that no longer exists must never read
  back as a legitimate cache hit under a new model's version.

## Separate, not fixed here

The diagnostic report also surfaced that a cache row with `raw_response_json
= None` (written when Gemini answers 2xx but the body never parses into
`GeminiSceneResult`, even after its one JSON-reinforcement retry) reads back
as a legitimate cache hit forever, indistinguishable from "no useful answer,
correctly recorded" versus "the model hiccuped once." That is a real,
narrower-than-first-suspected gap (transport failures and non-2xx statuses,
including this amendment's 404s, do **not** get cached — only a parse
failure on an actual 2xx does) left open deliberately, per the human's
instruction not to fix it in this prompt. Tracked as a follow-up: a
`failure_reason`/status column on `GeminiSceneCache` distinguishing "no
answer, cached" from "bad answer, cached" from "good answer, cached."

## Principle check

**P1–P5** — P5 (€0) checked explicitly: `gemini-3.5-flash` was confirmed
reachable on this key's free tier (`200` on a real `generateContent` call)
before being pinned; the fallback plan if it had required billing was
`gemini-2.5-flash`, itself found retired in the same probe. P4 (privacy)
unaffected — the same one-frame-per-scene boundary, enforced by
`SceneContext`'s own shape, applies to whichever model is on the other end
of the request. P1–P3 untouched.
