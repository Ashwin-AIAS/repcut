# Fix the dev launcher and the jobs socket — before criterion 16

Paste the PROMPT block below into Claude Code, on branch `prompt-02`.

Criterion 16 is the only thing standing between Prompt 02 and its gate. It
cannot be run, because `make dev` cannot be relied on to produce a working
stack. Two evenings have now been spent debugging a browser that was talking to
an orphaned process.

---

## Evidence

Two consecutive runs from one terminal session.

**Run 1 — healthy.** Engine on `ProactorEventLoop`, `Application startup
complete`, `/projects` and `/health` all 200, `/status` renders, UI serves four
routes. Then `^C` → `[dev] stopping…`.

**Run 2 — half-dead, reported as alive:**

```
[dev] engine → http://localhost:8000   ui → http://localhost:3000
[ui] ⨯ Failed to start server
[ui] Error: listen EADDRINUSE: address already in use :::3000
[engine] INFO:     Application startup complete.
```

`make dev` printed both URLs, the UI died on the line after, and the engine
carried on. Exit code stayed 0 until the user interrupted it.

**Browser, at the same time:** `localhost:3000` and `192.168.0.100:3000` both
`ERR_CONNECTION_REFUSED`; `127.0.0.1:8000` returns the engine's `{"detail":"Not
Found"}` (correct — no root route).

## Defects

| # | Defect | Evidence | Priority |
|---|---|---|---|
| D1 | `dev.sh` does not kill its child process tree on Windows. Ctrl-C leaves `next dev` (and previously `uvicorn`) holding a port. | Run 2's EADDRINUSE; the acknowledgement already written into `scripts/dev.sh` around line 86 | **Blocking** |
| D2 | `make dev` reports success while half the stack is dead. Printed "ui → localhost:3000" and kept running after the UI failed to bind. | Run 2 | **Blocking** |
| D3 | No preflight port check. Nothing tells the user which PID owns 3000/8000 or how to reclaim it. | Run 2 | High |
| D4 | `/ws/jobs` never connected, even in the healthy Run 1. Four page loads of the editor, zero WebSocket lines in uvicorn's log. UI shows "Connecting to the engine…" indefinitely. | Run 1 | **Blocking for criterion 16** |
| D5 | `/status` is green while the app is unusable. It checks engine version, data dir, FFmpeg, libx264, CUDA and the Gemini key — but not the jobs socket, which is the thing that was broken. | Status page screenshot vs. D4 | Medium |

## The pattern worth naming

This is the third time in Prompt 02 that a green signal accompanied a
non-working app:

1. `verify-02` 20/21 green while every upload 500'd (Windows event loop).
2. `/status` all-green while `/ws/jobs` never connects (D4/D5).
3. `make dev` printing both service URLs while the UI is dead (D2).

Each individually is a small bug. Together they are one flaw: **the project
verifies components and reports on components, and never asserts that the
assembled product works.** Fixes below should close that gap, not just the
three instances.

---

## PROMPT — dev launcher and jobs socket

### Role & Context

```
Continuing Prompt 02 on branch prompt-02. The gate is 22/23 with only the
human criterion 16 outstanding, but criterion 16 cannot be executed because
`make dev` cannot be trusted to produce a working stack. Fix that, then the
jobs socket, then extend the gate so neither can regress silently.

Read docs/prompts/fix-dev-launcher.md for the evidence and defect list. Work
autonomously per the autonomy protocol in CLAUDE.md.
```

### Checkpoints

Each is binary. Do not proceed past a red checkpoint; report instead.

**C1 — dev.sh kills what it started (D1).**
Ctrl-C, or any exit, must leave nothing listening on `$ENGINE_PORT` or
`$UI_PORT`. On Windows, `npm run dev` spawns `node` as a *grandchild*, so
killing the recorded PID is not enough — kill the tree (`taskkill //T //F`, or
resolve the port owner and kill that). The trap must fire on `INT`, `TERM` and
`EXIT`, and be idempotent.
**Pass:** start `make dev`, wait for both services, Ctrl-C, then assert both
ports are free. Repeat immediately — the second `make dev` must start cleanly
with no EADDRINUSE. Automate this as a script, not a manual check.

**C2 — dev.sh fails loudly when either side dies (D2).**
If the engine or the UI exits non-zero at any point, `make dev` prints a named
cause and the remedy, tears the other side down, and exits non-zero. It must
never print a service URL for a service that is not listening. Print the URLs
*after* both are confirmed accepting connections, not before.
**Pass:** occupy port 3000 with a dummy listener, run `make dev`, assert exit
code is non-zero, output names the port and the owning PID, and the engine is
not left running.

**C3 — preflight (D3).**
Before starting anything, check both ports. If occupied, print the owning PID
and the exact command to reclaim it, then exit non-zero. Do not kill another
process silently — a stray `taskkill` outside the repo is a destructive action
outside the autonomy protocol's remit.
**Pass:** covered by C2's test; add the PID-in-output assertion here.

**C4 — the jobs socket connects (D4).** *This is the one that blocks
criterion 16.*
Diagnose before fixing. In Run 1 the editor page loaded four times and uvicorn
logged no WebSocket at all — so either the client never opened it, or it was
rejected before uvicorn logged anything. Check both ends:
- Client: does the editor actually call `new WebSocket(...)` on mount, and what
  URL does it build? A `NEXT_PUBLIC_` base that is empty at build time
  produces a silently wrong URL.
- Server: `security.md` requires a per-route `Origin` check **before**
  `accept()`. If the allowlist holds `127.0.0.1:3000` and the browser sends
  `localhost:3000`, the socket is refused — same machine, different origin
  string. Verify the allowlist covers exactly the origins `make dev` serves.
Report which of the two it was before changing code.
**Pass:** with `make dev` up, opening a project logs a WebSocket accept in
uvicorn, and the Engine jobs panel leaves the "Connecting…" state within 2s.
Assert it in the gate against a real uvicorn, not in-process ASGI.

**C5 — `/status` and `/health` cover the socket (D5).**
`/health` gains a jobs-socket field; the status page renders it with the same
Yes/No treatment as "Can start video tools". A status page that is green while
the product cannot function is worse than no status page — it actively
misdirects.
**Pass:** with the socket deliberately broken, `/status` shows it red.

**C6 — the gate closes the class, not the instances.**
Add one criterion that starts the real stack the way a user starts it
(`make dev`), waits for both ports, opens a project, and asserts the jobs
socket connects. This is the assertion that would have caught D4 *and* the
event-loop bug *and* D2. If an existing criterion already boots a real uvicorn,
extend it rather than adding another boot.
**Pass:** `make verify-02` still 22/23 with criterion 16 the only failure, and
the new criterion demonstrably fails when the socket is broken (run the
negative control, do not assume it).

### Constraints

- `.claude/rules/` all apply. Especially: no raw traceback or absolute path in
  any output (`secrets.md`), named exceptions only (`code-style.md`), and no
  destructive action outside the repo — do not kill processes the launcher did
  not start.
- GNU Make 3.81, no `.ONESHELL`. Recipes stay one line chained with `&&`.
- Do not weaken any existing criterion to accommodate a new one.
- Scope discipline: this is a repair, not a feature. Nothing here should touch
  the media pipeline, the design system or the schema.

### Definition of done

C1–C6 all green, four to six focused commits on `prompt-02`, `make verify-02`
at 22/23 with criterion 16 the sole failure, `docs/reports/prompt-02.md`
updated with the defect list, the D4 root cause as found, and the "green signal,
broken product" pattern under Open issues.

Then stop. Criterion 16 is the human's, and it is next.
