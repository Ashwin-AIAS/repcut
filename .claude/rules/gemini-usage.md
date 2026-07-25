# Rule: Gemini API usage

Model: **Gemini 2.0 Flash, free tier.** Both P4 (privacy) and P5 (€0) are
enforced here, so this rule is strict.

## What may be sent
- **Sampled frames only: one frame per detected scene.** Plus scene metadata
  (duration, timestamp, detected motion energy).
- Never a full video. Never an audio track. Never a burst of frames "for better
  accuracy." Never the user's filename or file path.
- Strip EXIF/GPS from any frame before upload.
- The UI must disclose, at the moment it happens, that sampled frames are being
  sent.

## Caching — mandatory
- Cache key: `(video_hash, scene_id, prompt_version)`. Stored in SQLite.
- A re-edit, re-render, or app restart must **never** re-call the API for a
  scene already analyzed. Cache-miss on a repeat run is a bug.
- Cache entries survive across sessions and are inspectable.

## Rate limiting — client-side, not hope-based
- A token-bucket limiter enforces `GEMINI_RPM_LIMIT` and `GEMINI_DAILY_LIMIT`
  from config, before the request is made.
- Handle `429` and quota exhaustion with exponential backoff + jitter, capped.
  After the cap: degrade gracefully to heuristic scene tags, surface a clear
  UI message, and continue. The app must never hard-fail because a free quota
  ran out.
- Log every call's cost in requests, and keep a running daily counter.

## Responses
- Request structured JSON output and validate with a Pydantic schema. A
  malformed or hallucinated response is a handled error, not a crash.
- Never `eval()` or trust model output as code. For the copilot (Prompt 11),
  function calling maps to a **fixed allowlist** of edit operations with
  validated arguments — the model never constructs arbitrary calls.

## Key handling
`GEMINI_API_KEY` comes from `.env` via settings. Never logged, never in a
report, never in a test fixture, never in `.env.example`. See `secrets.md`.
