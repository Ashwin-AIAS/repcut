---
name: gemini-free-tier
description: Gemini 2.0 Flash client for Repcut — sampled-frame privacy boundary, SQLite caching, token-bucket rate limiting, backoff, graceful degradation, and function-calling allowlist. Use for any code calling Gemini.
---

# Gemini 2.0 Flash on the free tier

This is where **P4 (privacy)** and **P5 (€0)** are actually enforced. Both fail
here quietly if the client is careless.

## The privacy boundary — non-negotiable

Sent: **one sampled frame per detected scene**, plus scene metadata (duration,
timestamp, motion energy).

Never sent: a full video, an audio track, multiple frames per scene "for
accuracy," the filename, or any file path. Strip EXIF/GPS before upload. The UI
discloses the send when it happens.

A request to send more is a P4 conflict — stop and ask.

## Cache first, always

Conceptually, the cache key is `(video_hash, scene_id, prompt_version)`. As
shipped (Prompt 03, `docs/guide-amendments/008-...md`), `video_hash` is not
restated on the cache row — it is folded into `scene_id`'s own key instead,
because a `scenes` row is already unique per `(sha256, detector_params_version,
sequence_index)`, so which video a scene came from is implied by which scene
is being looked up:

```sql
CREATE TABLE scenes (
  id                       TEXT PRIMARY KEY,
  sha256                   TEXT NOT NULL REFERENCES media_blobs(sha256),
  detector_params_version  INTEGER NOT NULL,
  sequence_index           INTEGER NOT NULL,
  -- ...boundaries, sampled_frame_path, motion/audio energy...
  UNIQUE (sha256, detector_params_version, sequence_index)
);

CREATE TABLE gemini_scene_cache (
  id                     TEXT PRIMARY KEY,
  scene_id               TEXT NOT NULL REFERENCES scenes(id),
  gemini_prompt_version  INTEGER NOT NULL,
  raw_response_json      TEXT,           -- null for a cached "never parsed" answer
  -- ...content_type, exercise_guess, environment, lighting, energy_level...
  retrieved_at           TIMESTAMP NOT NULL,
  UNIQUE (scene_id, gemini_prompt_version)
);
```

Check the cache before every call, unconditionally. A re-edit, re-render, or
app restart must never re-analyze a scene. **A cache miss on a repeat run is a
bug** — and on a free tier, an expensive one.

`gemini_prompt_version` in the key means changing the analysis prompt
invalidates cleanly instead of returning stale shapes.

## Rate limit client-side, before the request

Token bucket enforcing `GEMINI_RPM_LIMIT` and `GEMINI_DAILY_LIMIT` from config.
Do not use caught 429s as flow control — by then you've already spent the
request.

Persist the daily counter; a restart must not reset the budget.

## Degrade gracefully, never hard-fail

On 429 or quota exhaustion: exponential backoff with jitter, capped at ~3
attempts. Then **fall back to heuristic scene tags** (motion energy, scene
length, audio level, brightness), tell the user plainly, and keep working.

Repcut must never become unusable because a free quota ran out mid-session.
Design the analysis consumer to accept degraded tags from the start.

## Responses are untrusted input

Request structured JSON; validate with Pydantic. Malformed or hallucinated
output is a handled error, not a crash. Never `eval()`. Never let a response
determine a filesystem path.

## Function calling (Prompt 11 copilot)

The model chooses from a **fixed allowlist** of edit operations with validated
argument schemas. It never constructs arbitrary calls, never touches the
filesystem, never gets a shell. Reject unknown function names loudly rather
than ignoring them.

Chat turns burn quota fast — cap turns per session.

## The key

`GEMINI_API_KEY` from `.env` via settings. Log `key set: true`, never the value
or a prefix. Never in a report, fixture, or `.env.example`. Tests mock the
client — zero live calls in the suite, ever.
