# Rule: Code style

## Python (engine/)
- Python 3.11. `ruff` (lint + format) and `mypy` must be clean before commit.
- Full type annotations on every public function. `Any` requires a comment
  justifying it.
- Pydantic v2 models for all API request/response shapes and all config.
- `structlog` JSON logging. No bare `print()` in engine code.
- **Catch named exceptions, never bare `except:` or `except Exception:`.**
  Name the failure the handler prevents in a comment above it.
- All I/O async. Blocking work (FFmpeg, torch) goes to a thread/process
  executor — never blocks the event loop.
- Every script idempotent: safe to re-run after a crash without corrupting
  state or duplicating rows.
- No hardcoded paths. Everything from `Settings` (pydantic-settings).

## TypeScript (ui/)
- `strict: true`. **No `any`.** Use `unknown` + narrowing.
- Server Components by default; `"use client"` only where interaction requires.
- No component libraries — the design system is ours (`.claude/skills/repcut-design-system`).
- Zod schemas at every API boundary; parse, do not cast.
- No `useEffect` for data fetching in Server Component contexts.

## Both
- Functions do one thing. If it needs "and" in the name, split it.
- Errors surface to the UI with a human-readable cause, never a raw traceback.
- Comments explain *why*, not *what*.
- No dead code, no commented-out blocks, no TODOs without an owner and a prompt
  number (`# TODO(prompt-07): …`).
