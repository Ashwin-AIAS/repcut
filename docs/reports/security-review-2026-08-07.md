# Security review — 2026-08-07

Full review of every tracked file plus the uncommitted Prompt 02 working tree.
Scope as requested: hardcoded secrets, SQL/command injection, XSS, unsafe file
handling and deserialisation, missing input validation, auth gaps.

**Branch:** `prompt-02` · **Reviewer:** Claude · **Files reviewed:** 121 tracked
+ 9 untracked working-tree modules

---

## Summary

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Engine had no `Host`/`Origin` boundary — DNS rebinding + localhost CSRF | **High** | Fixed |
| 2 | Job WebSocket readable cross-origin (CSWSH) | **High** | Fixed |
| 3 | Media-store path traversal; `absolute()` escaped `$DATA_DIR` | **High** | Fixed |
| 4 | Upload size declared without a ceiling → disk exhaustion | **Medium** | Fixed |
| 5 | `next@14.2.35` — 6 high-severity advisories, no CI watching | **Medium** | Fixed |
| 6 | UI shipped no security headers | **Medium** | Fixed |
| 7 | `/jobs?limit=-1` returned every row | **Low** | Fixed |
| 8 | FFmpeg `-i` accepted protocol URLs (latent SSRF) | **Low** | Fixed |
| 9 | `ENGINE_URL` accepted any scheme | **Low** | Fixed |
| 10 | No security lint ruleset, no dependency audit in CI | **Low** | Fixed |

### Clean — checked and found sound

- **No hardcoded secrets anywhere.** `.env` untracked and gitignored; full
  `git log --all -p` scan across every branch found no key material. The only
  matches were the rule files listing patterns to block.
- **No SQL injection.** Everything goes through SQLAlchemy ORM constructs with
  bound parameters. The one place SQL is built as a string (`_one_of` in
  `db/models.py`) interpolates enum members defined in Python, not input.
- **No command injection.** `create_subprocess_exec` with `list[str]` argv, no
  `shell=True` anywhere, no `os.system`/`os.popen`.
- **No unsafe deserialisation.** No `pickle`, `marshal`, `eval`, `exec`,
  `yaml.load`, or `__import__` in the codebase.
- **No XSS sinks.** No `dangerouslySetInnerHTML`, no `innerHTML`, no
  `new Function`. TypeScript `strict`, zero `any`, zero `@ts-ignore`.
- **No personal data leakage.** No OS username, home path or email in any
  tracked file. Path redaction in `ffmpeg_builder` and `check_env.py` is real
  and tested.
- **Error handling.** `api/errors.py` uses fixed sentences with no request echo
  and no paths. Exception handlers name their exceptions; no bare `except:`.

Enabling `ruff`'s `S` (flake8-bandit) ruleset across the engine produced **zero
pre-existing findings**. The codebase was already disciplined; the gaps were
architectural, in the layer nothing had been written to cover.

---

## Findings

### 1. No network boundary on the engine — High

**Where:** `engine/repcut/main.py` (no middleware of any kind)

The engine listens on loopback with no authentication — a deliberate,
defensible choice for a single-user local app. But loopback is not a boundary
against a browser:

- **DNS rebinding.** An attacker page re-answers its own hostname as `127.0.0.1`
  after first load. The browser keeps treating it as same-origin with the
  attacker's domain, so the attacker sends requests to the engine *and reads the
  replies*. The SOP does not help — it is the mechanism being fooled. `Host` is
  the only header still naming the attacker, and nothing was checking it.
- **Localhost CSRF.** CORS decides whether a response is *readable*; it never
  stops a request being *executed*. Prompt 02's new `POST /projects`,
  `POST /uploads`, `PUT /chunk` and `POST /finalize` were all callable from any
  page the user had open.

**Fix:** new `engine/repcut/security.py` — `TrustedHostMiddleware` with an exact
loopback allow-list (no wildcards), plus `CORSMiddleware` with explicit origins
and `allow_credentials=False`. Installed at import time, not in the lifespan:
httpx's `ASGITransport` never opens a lifespan scope, so lifespan-installed
middleware would be absent from every test while present in production.
`ENGINE_HOST` added to config, defaulting to `127.0.0.1`, read by `make dev`,
with a startup warning when it is not loopback.

### 2. Job WebSocket readable cross-origin — High

**Where:** `engine/repcut/api/jobs.py` → `/ws/jobs`

**CORS does not apply to WebSockets.** A browser will open
`ws://localhost:8000/ws/jobs` from any page on any domain, with no preflight,
and hand the script the stream. `CORSMiddleware` never sees a WebSocket scope.

The stream carries job ids, project ids, content hashes, failure causes and the
clips they came from — effectively a log of what the user films and when. Any
tab could read it.

**Fix:** `is_allowed_origin()` checked *before* `accept()`, closing with 1008 on
a foreign origin. A missing `Origin` is allowed — non-browser clients do not
send one and are not the threat.

### 3. Media-store path traversal — High

**Where:** `engine/repcut/media/store.py`

The module docstring promised "no path component derives from anything the user
typed". Nothing enforced it. `blob_directory`, `derived_directory`, `part_path`
and `project_directory` interpolated their arguments straight into a path, and
`PurePosixPath` normalises nothing — a `sha256` of `../../../../etc/passwd`
produced exactly that path.

Worse, `absolute()` was `data_dir / Path(stored)`, and `stored` is read back
from a `String(512)` column with no CHECK constraint. Two silent escapes:
`Path("/data") / Path("/etc/shadow")` is `/etc/shadow` (an absolute join
*replaces* the base), and `..` segments survive the join.

**Fix:** every identifier validated against its shape (hex digest, UUID, slug)
before becoming a path component, raising `UnsafeStorePathError`. `absolute()`
now rejects absolute/drive-qualified paths and resolves both sides before
confirming containment — which also catches a symlink planted inside the store.

### 4. Upload size without a ceiling — Medium

**Where:** `engine/repcut/api/schemas.py` → `UploadCreate.size_bytes`

`Field(ge=0)` bounded below and not above, and `_write_body` uses the declared
size as its own write budget. A client declaring 10TB was granted a 10TB budget.
Filling `$DATA_DIR` does not just fail the upload — it takes the SQLite database
and every in-flight render with it.

**Fix:** `MAX_UPLOAD_BYTES` (64 GiB) and `MAX_CHUNK_BYTES` (256 MiB).

### 5. `next@14.2.35` — 6 high-severity advisories — Medium

14.2.35 is the last release on the 14 line; there is no patch. Advisories cover
SSRF in Server Actions and rewrites, cache poisoning in RSC responses, XSS via
CSP nonces and `beforeInteractive`, request smuggling, and several DoS paths.
Most touch features Repcut does not use yet — but "yet" is doing real work in
that sentence, and nothing in CI was watching.

**Fix (approved by Ashwin):** upgraded to `next@16.3.0` with
`eslint@9.39.5` + `eslint-config-next@16.3.0`. React stays on 18 (Next 16 peers
allow it) to keep the change small. Required an ESLint flat-config migration
(`.eslintrc.json` → `eslint.config.mjs`; `eslint-config-next@16` exports a
native flat array, so no `FlatCompat` shim) and a `lint` script change, because
Next 16 removed `next lint`. `npm audit`: **0 vulnerabilities**. Lint, tsc,
vitest (15 tests) and `next build` all verified green post-upgrade.

### 6. No security headers on the UI — Medium

**Where:** `ui/next.config.mjs` (was empty)

`frame-ancestors 'none'` is the load-bearing one: without it any page could
iframe the editor and clickjack its controls. The rest matter more as the app
starts rendering values the user does not control — filenames, transcribed
captions, Gemini scene descriptions.

**Fix:** CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
`Permissions-Policy`, and `poweredByHeader: false`. `connect-src` is limited to
self plus the engine, so the browser cannot phone home (P4). Verified live
against a production build — all six headers served on `/` and `/status`.

### 7. `/jobs?limit=-1` returned every row — Low

`min(limit, 200)` capped the top and let anything through underneath. SQLite
reads `LIMIT -1` as *no limit*. **Fix:** `Query(default=50, ge=1, le=200)` —
the framework refuses the value rather than reinterpreting it.

### 8. FFmpeg accepted protocol sources — Low (latent)

The value after `-i` is parsed by FFmpeg, not by a shell. `http://`, `rtmp://`,
`concat:` and `subfile:` make FFmpeg open a network or composite input — SSRF
with the user's home network as the reachable surface, and a P4 breach the
moment it succeeds. Not reachable today (sources come from the
content-addressed store); the check keeps it unreachable when that stops being
the only caller. **Fix:** `_checked_source()` in `ffmpeg_builder`, applied to
all three builders. Windows drive letters still parse.

### 9. `ENGINE_URL` accepted any scheme — Low

`z.string().url()` passes `file:`, `javascript:`, `data:`. The value is
interpolated into a `fetch` made by the Next *server*. **Fix:** scheme
restricted to http/https.

### 10. No security lint, no dependency audit — Low

**Fix:** `ruff`'s `S` ruleset enabled (only `S603`/`S607` globally ignored, both
documented — argv-list FFmpeg calls resolved through PATH). New CI job runs
`pip-audit --strict` and `npm audit --omit=dev --audit-level=high`.

---

## Tests

`engine/tests/test_security.py` — 30 regression tests, one per finding class:
host binding, WebSocket origin (including the substring-match case
`http://localhost:3000.evil.example`), digest/UUID/slug validation, `absolute()`
containment including a symlink escape, and FFmpeg protocol refusal.

Each is written to fail against the pre-fix code. A finding without a failing
test is a comment, not a fix.

## Verification status

| Check | Result |
|---|---|
| `ruff check engine` (with S ruleset) | Clean on all reviewed files |
| `ruff format --check` | Clean on all reviewed files |
| UI `tsc --noEmit` | Pass |
| UI `eslint . --max-warnings 0` | Pass |
| UI `vitest run` | 15/15 pass |
| UI `next build` | Pass |
| `npm audit --omit=dev` | 0 vulnerabilities |
| Security headers served | Verified live on `/` and `/status` |
| Fix logic (33 assertions, isolated) | Pass |
| `pytest engine` | **Not run — see below** |

**`make test` and `make verify-02` must be run on your machine.** The review
sandbox has only Python 3.10 and the engine requires ≥3.11 (`StrEnum`,
`datetime.UTC`), so the engine suite could not execute here. Every changed
module was AST-parsed, linted, and its logic verified in isolation, but that is
not the same as a green suite.

Two pre-existing lint failures in the parallel Prompt 02 working tree
(`tests/conftest.py` ASYNC240, `tests/test_uploads.py` import order + one
format diff) were left alone deliberately — they are someone else's uncommitted
work, and `ruff format` would conflict with it.

## Follow-ups

- Run `make lint && make test && make verify-02` locally before gating.
- Prompt 12 should fold `test_security.py` into the standing suite and add the
  `.part`-file orphan collection already deferred by amendment 004 — abandoned
  uploads still accumulate.
- Consider a CHECK constraint on `media_blobs.stored_path` asserting it is
  relative. `absolute()` now enforces it at every read; the constraint would
  stop a bad row being written in the first place. Cheap to add with the next
  migration, not worth one on its own.
