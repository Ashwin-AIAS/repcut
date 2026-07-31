#!/usr/bin/env bash
# Run the engine (:8000) and the UI (:3000) concurrently in one terminal.
# Output from both is prefixed and interleaved; Ctrl-C stops both.
#
# Lives in a script rather than the Makefile because `make` on Windows may hand
# recipe lines to cmd.exe, which cannot express job control or traps.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

ENGINE_PORT="${ENGINE_PORT:-8000}"
UI_PORT="${UI_PORT:-3000}"
ENGINE_URL="${ENGINE_URL:-http://localhost:${ENGINE_PORT}}"

# Read ports/URL from .env when the shell has not already set them. Only these
# three non-secret keys are lifted; GEMINI_API_KEY is deliberately NOT exported
# to the UI process — the engine reads it itself via pydantic-settings.
if [ -f .env ]; then
  for key in ENGINE_PORT UI_PORT ENGINE_URL; do
    if [ -z "${!key:-}" ] || ! printenv "$key" >/dev/null 2>&1; then
      value=$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"'\r')
      [ -n "$value" ] && export "$key=$value"
    fi
  done
  ENGINE_URL="${ENGINE_URL:-http://localhost:${ENGINE_PORT}}"
fi
export ENGINE_URL

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

echo "[dev] engine → http://localhost:${ENGINE_PORT}   ui → http://localhost:${UI_PORT}"
echo "[dev] status page → http://localhost:${UI_PORT}/status"
echo

"$PY" -m uvicorn repcut.main:app --host 127.0.0.1 --port "$ENGINE_PORT" --reload \
  2>&1 | prefix engine &
pids+=("$!")

( cd ui && npm run dev -- --port "$UI_PORT" ) 2>&1 | prefix ui &
pids+=("$!")

wait -n 2>/dev/null || wait
