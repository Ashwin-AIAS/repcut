#!/usr/bin/env bash
# Run the engine (:8000) and the UI (:3000) concurrently in one terminal.
# Output from both is prefixed and interleaved; Ctrl-C stops both.
#
# Lives in a script rather than the Makefile because `make` on Windows may hand
# recipe lines to cmd.exe, which cannot express job control or traps.
#
# Three properties this script owes the person running it, each paid for:
#
#   1. It kills what it started. Ctrl-C used to leave `next dev` holding :3000,
#      so the next run's UI died on EADDRINUSE while the browser kept talking to
#      the orphan from the previous run - for two evenings.
#   2. It never claims a service is up that is not. It used to print both URLs
#      before either process had bound anything, then keep running at exit code
#      0 with half the stack dead.
#   3. It refuses a port it does not own, and says who does. It does not kill
#      that process: something outside this repository is not this script's to
#      terminate.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
  *) IS_WINDOWS=0 ;;
esac

# Read ports/URL from .env when the shell has not already set them. Only these
# non-secret keys are lifted; GEMINI_API_KEY is deliberately NOT exported to the
# UI process — the engine reads it itself via pydantic-settings.
env_value() {  # $1 = key. Prints the .env value, or nothing.
  [ -f .env ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"'\r'
}

# Captured before anything is resolved, because a *derived* value has to follow
# the more specific source. `.env` shipping ENGINE_URL=http://localhost:8000 beat
# a shell `ENGINE_PORT=8010`, so the engine listened on 8010 while the UI - and
# the Content-Security-Policy built from the same value - still pointed at 8000.
# The old comment below claimed this was fixed; it was fixed only for the case
# where .env had no ENGINE_URL at all.
shell_engine_port="${ENGINE_PORT:-}"
shell_engine_url="${ENGINE_URL:-}"

# Precedence: shell environment > .env > default.
ENGINE_PORT="${ENGINE_PORT:-$(env_value ENGINE_PORT)}"
ENGINE_PORT="${ENGINE_PORT:-8000}"
UI_PORT="${UI_PORT:-$(env_value UI_PORT)}"
UI_PORT="${UI_PORT:-3000}"

# A port is used to build URLs, to probe sockets and to match netstat output. A
# non-numeric value would silently match nothing and be reported as "free".
for pair in "ENGINE_PORT:$ENGINE_PORT" "UI_PORT:$UI_PORT"; do
  name="${pair%%:*}"; value="${pair#*:}"
  case "$value" in
    ''|*[!0-9]*) echo "[dev] $name must be a port number, got '$value'" >&2; exit 1 ;;
  esac
  if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
    echo "[dev] $name must be between 1 and 65535, got '$value'" >&2; exit 1
  fi
done

# ENGINE_URL is derived LAST so it always tracks the port actually in use, and
# the rule is: it must address the engine THIS SCRIPT is about to start.
#
#   1. A shell-set ENGINE_URL wins outright — that is someone deciding, now.
#   2. A shell-set ENGINE_PORT beats a stored ENGINE_URL, because the specific
#      override has to win over the general stored value.
#   3. Otherwise .env's ENGINE_URL, then the derived default.
#
# Whatever survives is then checked against ENGINE_PORT: an address pointing at
# a port this script is not starting anything on is wrong by construction, and
# it fails as "engine unreachable" three layers away from its cause.
if [ -n "$shell_engine_url" ]; then
  ENGINE_URL="$shell_engine_url"
elif [ -n "$shell_engine_port" ]; then
  ENGINE_URL="http://localhost:${ENGINE_PORT}"
else
  ENGINE_URL="$(env_value ENGINE_URL)"
  ENGINE_URL="${ENGINE_URL:-http://localhost:${ENGINE_PORT}}"
fi

# Only an *explicit* port is compared. A URL without one is a proxy on 80/443,
# which is a deliberate setup this check has no business rewriting.
engine_url_port="$(printf '%s' "$ENGINE_URL" | sed -E 's#^[a-zA-Z]+://[^/:]+:?([0-9]*).*#\1#')"
if [ -n "$engine_url_port" ] && [ "$engine_url_port" != "$ENGINE_PORT" ]; then
  if [ -n "$shell_engine_url" ]; then
    echo "[dev] warning: ENGINE_URL names port $engine_url_port but the engine starts on $ENGINE_PORT" >&2
  else
    echo "[dev] ENGINE_URL named port $engine_url_port; the engine starts on $ENGINE_PORT — using the latter" >&2
    echo "[dev]   fix: set ENGINE_URL in .env to match ENGINE_PORT, or remove it and let it derive" >&2
    ENGINE_URL="http://localhost:${ENGINE_PORT}"
  fi
fi

# The same address again, for the browser this time. `ENGINE_URL` is read by
# Server Components only; the client bundle reads `NEXT_PUBLIC_ENGINE_URL`, and
# it is also what `next.config.mjs` builds the Content-Security-Policy from. Left
# unset, both fall back to :8000 — so on any other ENGINE_PORT the browser would
# call one address while the server called another, and CSP would block the
# difference. Exported here so one port setting reaches all three.
NEXT_PUBLIC_ENGINE_URL="${NEXT_PUBLIC_ENGINE_URL:-$(env_value NEXT_PUBLIC_ENGINE_URL)}"
NEXT_PUBLIC_ENGINE_URL="${NEXT_PUBLIC_ENGINE_URL:-$ENGINE_URL}"

# The bind address comes from config like everything else, and defaults to
# loopback. The engine has no authentication, so binding it anywhere else
# publishes the user's footage to the network - repcut/security.py logs a
# warning when this is not a loopback address.
ENGINE_HOST="${ENGINE_HOST:-$(env_value ENGINE_HOST)}"
ENGINE_HOST="${ENGINE_HOST:-127.0.0.1}"
export ENGINE_HOST ENGINE_PORT UI_PORT ENGINE_URL NEXT_PUBLIC_ENGINE_URL

# A wildcard bind is not an address you can connect to on every stack. Probe
# loopback in that case, which is where it will also be listening.
PROBE_HOST="$ENGINE_HOST"
case "$PROBE_HOST" in 0.0.0.0|::|'[::]'|'') PROBE_HOST="127.0.0.1" ;; esac

PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No virtualenv found. Run: make setup" >&2
  exit 1
fi

# --- ports ------------------------------------------------------------------

# Whether anything is accepting connections on a port. Bash's own /dev/tcp, so
# no curl, no nc, and nothing to install (P5).
port_open() {  # $1 = host, $2 = port
  (exec 3<>"/dev/tcp/$1/$2") >/dev/null 2>&1
}

# The pid(s) listening on a port, one per line, empty when nothing is.
port_pids() {  # $1 = port
  if [ "$IS_WINDOWS" = 1 ]; then
    netstat -ano -p tcp 2>/dev/null \
      | awk -v want="$1" '$1=="TCP" && $4=="LISTENING" {
          n = split($2, parts, ":"); if (parts[n] == want) print $5
        }' | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | sort -u
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
  fi
}

# The command that frees a port, spelled for the shell `make dev` runs in.
reclaim_command() {  # $1 = pid
  if [ "$IS_WINDOWS" = 1 ]; then
    echo "taskkill //PID $1 //T //F"
  else
    echo "kill -TERM $1"
  fi
}

# Preflight. Refuse a port someone else holds, name who holds it, and say how to
# take it back — but do not take it back. A stray taskkill against a pid this
# script did not start is a destructive action outside the repository.
preflight() {
  blocked=0
  for pair in "engine:$ENGINE_PORT" "ui:$UI_PORT"; do
    name="${pair%%:*}"; port="${pair#*:}"
    owners="$(port_pids "$port")"
    if [ -z "$owners" ] && ! port_open 127.0.0.1 "$port"; then
      continue
    fi
    blocked=1
    if [ -n "$owners" ]; then
      for pid in $owners; do
        echo "[dev] port $port ($name) is already in use by PID $pid" >&2
        echo "[dev]   reclaim it:  $(reclaim_command "$pid")" >&2
      done
    else
      echo "[dev] port $port ($name) is already in use; the owning process could not be identified" >&2
    fi
    echo "[dev]   or pick another port: set $(echo "$name" | tr '[:lower:]' '[:upper:]')_PORT in .env" >&2
  done
  [ "$blocked" = 0 ]
}

if ! preflight; then
  echo "[dev] nothing was started." >&2
  exit 1
fi

# Ports this script bound itself. Preflight proved each was free immediately
# beforehand, which is what makes `release_port` safe: any listener on one of
# these at shutdown is a process this script started, not a stranger's.
owned_ports=()

# --- children ---------------------------------------------------------------

pids=()
names=()
cleaned=0

# Git Bash reports an MSYS pid; taskkill needs the native Windows pid, which
# Git Bash exposes at /proc/<pid>/winpid.
kill_tree() {
  local pid="$1" wpid
  wpid=$(cat "/proc/${pid}/winpid" 2>/dev/null || echo "$pid")
  if [ "$IS_WINDOWS" = 1 ] && command -v taskkill >/dev/null 2>&1; then
    # //T because `npm run dev` reaches node as a GRANDCHILD: killing the pid
    # this script recorded leaves the process that actually holds :3000 running.
    taskkill //PID "$wpid" //T //F >/dev/null 2>&1 || true
  fi
  kill -TERM "$pid" >/dev/null 2>&1 || true
}

# Wait for a port this script bound to go quiet, and take it back if it will
# not. Only ever called on a port in `owned_ports`.
release_port() {  # $1 = port
  local port="$1" waited=0 pid
  while [ "$waited" -lt 40 ]; do
    port_open 127.0.0.1 "$port" || return 0
    sleep 0.25
    waited=$((waited + 1))
  done
  for pid in $(port_pids "$port"); do
    if [ "$IS_WINDOWS" = 1 ]; then
      taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    else
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  done
  waited=0
  while [ "$waited" -lt 40 ]; do
    port_open 127.0.0.1 "$port" || return 0
    sleep 0.25
    waited=$((waited + 1))
  done
  echo "[dev] port $port is still held after shutdown; run \`make dev\` again to see who by" >&2
  return 1
}

# Idempotent: disarms itself first, so the EXIT trap firing after the INT trap
# does not kill a second time, and re-entry during a slow taskkill is a no-op.
cleanup() {
  [ "$cleaned" = 1 ] && return 0
  cleaned=1
  trap - INT TERM EXIT
  echo
  echo "[dev] stopping…"
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  wait >/dev/null 2>&1 || true
  for port in "${owned_ports[@]:-}"; do [ -n "$port" ] && release_port "$port"; done
}

# A signal handler that returns is not enough: bash resumes the supervision loop
# afterwards, finds both children dead, and reports a crash for what was a
# deliberate stop. So the handler ends the script itself.
#
# Exit 0, because Ctrl-C is the documented way to stop `make dev` and a stop the
# user asked for succeeded. A non-zero status here would print `make: *** Error
# 130` after every single run and teach the user to ignore make's exit codes -
# which are the same codes the failure paths below rely on being noticed.
on_signal() {
  cleanup
  exit 0
}

# Tear the stack down and say why, when one half died and the other is still up.
# Never leaves the survivor running: half a stack in a terminal that has already
# scrolled is exactly how a browser ends up talking to an orphan.
die() {  # $1 = service name, $2 = exit code, $3 = port
  local name="$1" code="$2" port="$3" pid
  echo >&2
  echo "[dev] the $name exited with code $code — the stack is not running" >&2
  for pid in $(port_pids "$port"); do
    echo "[dev]   port $port is held by PID $pid: $(reclaim_command "$pid")" >&2
  done
  echo "[dev]   cause: see the [$name] lines above; they are the only report of it" >&2
  cleanup
  exit 1
}

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

trap on_signal INT
trap on_signal TERM
trap cleanup EXIT

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
pids+=("$!"); names+=("engine"); owned_ports+=("$ENGINE_PORT")

( cd ui && npm run dev -- --port "$UI_PORT" ) > >(prefix ui) 2>&1 &
pids+=("$!"); names+=("ui"); owned_ports+=("$UI_PORT")

engine_pid="${pids[0]}"
ui_pid="${pids[1]}"

# Wait for a service to accept a connection, and fail the moment it stops being
# a possibility — a dead process is answered immediately rather than after the
# full timeout.
await_service() {  # $1 = name, $2 = pid, $3 = host, $4 = port, $5 = seconds
  local name="$1" pid="$2" host="$3" port="$4" limit="$5" waited=0 code
  while [ "$waited" -lt "$((limit * 4))" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"; code=$?
      die "$name" "$code" "$port"
    fi
    port_open "$host" "$port" && return 0
    sleep 0.25
    waited=$((waited + 1))
  done
  echo >&2
  echo "[dev] the $name did not accept a connection on port $port within ${limit}s" >&2
  echo "[dev]   cause: see the [$name] lines above" >&2
  cleanup
  exit 1
}

# The engine migrates and opens the database before it binds; the UI compiles a
# turbopack graph. Neither is fast on a cold start, and a timeout that fires
# before a healthy service is ready is a false alarm the user cannot act on.
await_service engine "$engine_pid" "$PROBE_HOST" "$ENGINE_PORT" 90
await_service ui "$ui_pid" 127.0.0.1 "$UI_PORT" 180

# Printed here and nowhere earlier: a URL on screen is this script asserting the
# service behind it is accepting connections, and it now is.
echo
echo "[dev] engine → http://localhost:${ENGINE_PORT}   ui → http://localhost:${UI_PORT}"
echo "[dev] status page → http://localhost:${UI_PORT}/status"
echo

# Supervise. `wait -n` would say that *a* job ended without saying which, and
# the whole point is to name the one that died.
while :; do
  for index in 0 1; do
    pid="${pids[$index]}"
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"; code=$?
      port="$ENGINE_PORT"; [ "$index" = 1 ] && port="$UI_PORT"
      die "${names[$index]}" "$code" "$port"
    fi
  done
  sleep 1
done
