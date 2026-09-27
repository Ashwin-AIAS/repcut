# Rule: Application security

`secrets.md` covers credentials. This covers everything else — the engine as a
service, the UI as an origin, and the footage as data worth stealing.

## The threat model, stated plainly

Repcut has **no accounts and no authentication**, deliberately: it is one
person's engine on one person's laptop, and P5 rules out an auth service. That
is defensible only because the boundary is drawn elsewhere. Write it down so it
is not quietly relied on:

| Assumed trusted | Assumed hostile |
|---|---|
| The person at the keyboard | Every web page they have open in another tab |
| Processes they started themselves | Any other device on the same network |
| Files they chose to upload | The *contents* of those files |
| The local disk | Anything read back from the database or ffprobe |

The consequential row is the second one. A browser tab is a program an attacker
controls, running on the trusted machine, able to send requests to `localhost`.
"Bound to loopback" is not an authentication mechanism.

## The engine's network boundary

`repcut/security.py` holds this and must not be bypassed.

- **Host allow-list** (`TrustedHostMiddleware`), exact values, never `"*"` and
  never a leading-dot wildcard. This is the DNS-rebinding defence: an attacker
  who re-points their own hostname at `127.0.0.1` is same-origin with their own
  page, so the SOP does not help, and `Host` is the only field still naming
  them.
- **CORS**, explicit origins only, `allow_credentials=False`. CORS governs
  whether a reply is *readable*; it never stops a request being *executed*.
  Treat every mutating route as reachable from any page and design accordingly.
- **WebSocket `Origin` checks, per route, before `accept()`.** CORS middleware
  never sees a WebSocket scope. A socket without this check is readable by every
  page on the internet that can reach the port.
- **Bind loopback.** `ENGINE_HOST` defaults to `127.0.0.1`, `make dev` reads it,
  and the engine warns at startup if it is anything else. Never hardcode a bind
  address in a script.

New route checklist: does it mutate state, and would that be acceptable if
triggered by an unrelated web page? If not, it needs more than CORS.

## Input validation

- **Every** request field is a Pydantic model with bounds. Not just a type — a
  `min`/`max` for numbers, `max_length` for strings, a `pattern` for anything
  with a shape (digests, ids).
- **Bound every list endpoint.** `limit: int = Query(default=…, ge=1, le=…)`.
  Never `min(limit, N)`: that caps the top and lets `-1` through, and SQLite
  reads `LIMIT -1` as no limit at all.
- **Bound every upload.** A declared size becomes a write budget. Unbounded, it
  is a disk-fill that takes the database with it.
- Data from **ffprobe, the database and the filesystem is input too.** A
  subprocess's stdout and a column read back are not more trustworthy than a
  request body; they are just less obviously untrusted.

## Paths — the store is the only path builder

- Every filesystem path comes from `repcut/media/store.py`. Never build one by
  string concatenation or f-string anywhere else.
- Every identifier that becomes a path component is **validated against its
  shape first** — hex digest, UUID, slug. `PurePosixPath` does not normalise
  `..`, so an unvalidated id lands wherever it says.
- `store.absolute()` is the only way to turn a stored path into a real one, and
  it refuses anything that resolves outside `$DATA_DIR`. Both sides are
  `resolve()`d, so a symlink planted in the store is caught here rather than at
  the `open()`.
- A path joined from an absolute path *replaces* the base — `Path("/data") /
  Path("/etc/shadow")` is `/etc/shadow`, silently. Never rely on the join.

## Subprocesses

- `list[str]` argv, `shell=False`, always (`ffmpeg.md`). No exceptions.
- **The value after `-i` is parsed by FFmpeg, not by a shell.** `http://`,
  `rtmp://`, `concat:` and `subfile:` are inputs FFmpeg will happily open —
  which turns a source path into SSRF and a P4 breach. `ffmpeg_builder`
  validates sources; nothing else may call FFmpeg.

## The UI

- Never `dangerouslySetInnerHTML`. If a case seems to require it, it does not.
- Security headers live in `next.config.mjs` and are part of the build, not a
  deployment concern. `frame-ancestors 'none'` is load-bearing: without it any
  page can iframe the editor and clickjack a destructive control.
- Zod-parse at every boundary, including the engine's own responses. A local
  service is still a boundary.
- No secret ever reaches the client bundle. `NEXT_PUBLIC_` is a decision, not a
  convenience — nothing sensitive may carry that prefix.

## Errors

- A message the UI renders is a **fixed sentence**, never an f-string over
  request input (that makes the error page a reflection surface) and never a
  path (which carries the OS username — `secrets.md`).
- Never a traceback, never a raw stderr dump. `api/errors.py` is the vocabulary.

## Dependencies

- `pip-audit` and `npm audit --omit=dev --audit-level=high` run in CI. Both are
  free (P5).
- A failing audit is fixed by upgrading, never by an ignore entry. If an upgrade
  is genuinely infeasible this week, write the risk acceptance into
  `docs/reports/` with a date and a named follow-up — do not silence the job.
- `ruff`'s `S` (flake8-bandit) ruleset is on for **both `engine/` and
  `scripts/`** (prompt-03) — the entire scope of `make lint`, of CI's ruff
  step, and of the pre-commit hooks (`files: ^(engine|scripts)/`). Two
  separate `pyproject.toml`s carry it, not one shared file: `engine/` is also
  the installed package's build config, and `scripts/` is not a package, so a
  root-level `pyproject.toml` holds just `[tool.ruff]` for it. `S603`/`S607`
  are ignored in both, for the documented FFmpeg/subprocess reason. Any new
  `# noqa: S…` states what it prevents, in a comment.
- `scripts/` holds every process-spawning line outside the builder —
  `posix_shell.py`, `dev_stack.py`, `cdp_browser.py` — and until prompt-03 its
  `# noqa: S…` directives were written against a scanner that had never read
  the file. That gap is closed; a clean `make lint` covers `scripts/` now.

## Never

- Weaken a security gate to get a build green.
- Add a CORS `"*"`, a `TrustedHost` wildcard, or `allow_credentials=True`.
- Send footage, audio, or more than one sampled frame per scene anywhere
  (`gemini-usage.md`, P4).
- Introduce a route that mutates state without asking the browser-tab question
  above.
