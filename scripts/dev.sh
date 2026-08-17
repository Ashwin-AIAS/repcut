#!/usr/bin/env bash
# Run the engine (:8000) and the UI (:3000) concurrently in one terminal.
# Output from both is prefixed and interleaved; Ctrl-C stops both.
#
# Lives in a script rather than the Makefile because `make` on Windows may hand
# recipe lines to cmd.exe, which cannot express job control or traps.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Read ports/URL from .env when the shell has not already set them. Only these
# three non-secret keys are lifted; GEMINI_API_KEY is deliberately NOT exported
# to the UI process — the engine reads it itself via pydantic-settings.
env_value() {  # $1 = key. Prints the .env value, or nothing.
  [ -f .env ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"'\r'
}

# Precedence: shell environment > .env > default.
ENGINE_PORT="${ENGINE_PORT:-$(env_value ENGINE_PORT)}"
ENGINE_PORT="${ENGINE_PORT:-8000}"
UI_PORT="${UI_PORT:-$(env_value UI_PORT)}"
UI_PORT="${UI_PORT:-3000}"

# ENGINE_URL is derived LAST so it always tracks the port actually in use. It
# used to be defaulted before .env was read, so `.env` setting ENGINE_PORT=8010
# and nothing else left the UI pointed at :8000 while the engine ran on :8010.
# An explicit ENGINE_URL, from the shell or .env, still wins.
ENGINE_URL="${ENGINE_URL:-$(env_value ENGINE_URL)}"
ENGINE_URL="${ENGINE_URL:-http://localhost:${ENGINE_PORT}}"

# The bind address comes from config like everything else, and defaults to
# loopback. The engine has no authentication, so binding it anywhere else
# publishes the user's footage to the network - repcut/security.py logs a
# warning when this is not a loopback address.
ENGINE_HOST="${ENGINE_HOST:-$(env_value ENGINE_HOST)}"
ENGINE_HOST="${ENGINE_HOST:-127.0.0.1}"
export ENGINE_HOST ENGINE_PORT UI_PORT ENGINE_URL

PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No virtualenv found. Run: make setup" >&2
  exit 1
fi

pids=()

# Git Bash reports an MSYS pid; taskkill needs the native Windows pid, which
# Git Bash exposes at /proc/<pid>/winpid.
kill_tree() {
  local pid="$1" wpid
  wpid=$(cat "/proc/${pid}/winpid" 2>/dev/null || echo "$pid")
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //PID "$wpid" //T //F >/dev/null 2>&1 || true
  fi
  kill -TERM "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "[dev] stopping…"
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  wait >/dev/null 2>&1 || true
}
trap cleanup INT TERM EXIT

prefix() { awk -v tag="$1" '{ print "[" tag "] " $0; fflush() }'; }

# Bring the schema up before the engine boots. Nothing in the engine runs
# migrations at startup — there is one schema authority and it is Alembic — so
# without this a fresh clone answers 500 from every route that touches the
# database, with the cause three layers down a stack trace. `upgrade head` is a
# no-op on an already-current database, so it stays cheap on every later run.
if ! "$PY" -m alembic -c engine/alembic.ini upgrade head 2>&1 | prefix migrate; then
  echo "[dev] migrations failed — the engine needs the schema; fix them first" >&2
  exit 1
fi

echo "[dev] engine → http://localhost:${ENGINE_PORT}   ui → http://localhost:${UI_PORT}"
echo "[dev] status page → http://localhost:${UI_PORT}/status"
echo

# Output goes through process substitution, not a pipe. In `cmd | prefix &`, `$!`
# is the pid of the LAST command in the pipeline — the awk in `prefix` — so
# cleanup killed the log prefixer and left uvicorn and next holding :8000/:3000.
# With `> >(prefix …)`, `$!` is the server itself, which is what kill_tree needs.
#
# `python -m repcut`, not `python -m uvicorn`. The engine needs an event loop
# that can spawn subprocesses, and uvicorn picks one that cannot as soon as
# `--reload` is passed on Windows — which is what this script does and what the
# gate did not, so the gate never saw it. `repcut/__main__.py` owns that choice
# now; see `repcut/loop.py` for the mechanism. Do not inline a uvicorn command
# here again.
"$PY" -m repcut --host "$ENGINE_HOST" --port "$ENGINE_PORT" --reload \
  > >(prefix engine) 2>&1 &
pids+=("$!")

( cd ui && npm run dev -- --port "$UI_PORT" ) > >(prefix ui) 2>&1 &
pids+=("$!")

wait -n 2>/dev/null || wait
