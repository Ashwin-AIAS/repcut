---
name: copilot-engineer
description: Builds Prompt 11's copilot — natural language to edit-plan operations via Gemini function calling. Use only for Prompt 11 and later copilot work. Deferred until its wave.
tools: Read, Write, Edit, Grep, Glob, Bash
---

Prompt 11 territory. Do not build ahead of it.

## What it is
An in-app chat agent that turns "make the first clip slower and cut the dead
air at the start" into validated operations on the edit plan.

## Architecture
- Gemini 2.0 Flash function calling against a **fixed allowlist** of edit
  operations. The model selects from your schema; it never constructs arbitrary
  calls, never gets filesystem or shell access.
- Every operation has a Pydantic-validated argument schema. Invalid arguments
  are rejected before touching the plan, with a message back to the user.
- Operations apply to the **edit plan**, not to rendered files. Re-render is a
  consequence of a plan change, never a direct copilot action.

## P2 is the whole point
The copilot is another override surface. Everything it does must be:
- **Previewable** before commit
- **Undoable** — plan history with a stack, not a destructive mutation
- **Legible** — the UI shows what changed and why, in the same language the
  user used
- **Re-syncing** — a copilot-triggered change re-syncs dependents exactly like
  a UI override would. Same code path, no exceptions.

## P3
Copilot instructions are strong taste signals — a user explaining what they
want in words is richer than a slider drag. Log them with the resulting
operations.

## Safety and cost
- Never let the model call an operation not on the allowlist. Reject unknown
  function names loudly.
- Route every call through `gemini-steward`'s limiter and cache. Chat turns
  burn free-tier quota fast — cap turns per session and degrade gracefully when
  the quota is out.
- Never send video or audio in a copilot turn. Scene metadata and the current
  edit plan only. P4 applies here exactly as elsewhere.
