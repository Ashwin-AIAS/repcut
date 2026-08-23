#!/usr/bin/env bash
# Gate for Prompt 01 — engine + UI scaffold, dev environment, /health contract.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_00.sh.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (verbatim, one block each, in order):
#   1  scaffold shape: engine/pyproject.toml, engine/repcut/main.py,
#      engine/repcut/config.py, engine/tests/, ui/package.json,
#      ui/app/status/page.tsx, scripts/check_env.py all exist
#   2  ruff check / ruff format --check / mypy (strict config) all exit 0
#   3  pytest engine -m "not gpu" green
#   4  /health live on an ephemeral port: 200 + all ten fields, type-valid
#   5  CPU fallback proven: torch unimportable -> 200, cuda=false, device=cpu
#   6  UI clean: tsc --noEmit, npm run lint, npm run test
#   7  UI builds: npm run build
#   8  no `any` in ui/**/*.{ts,tsx}
#   9  scripts/check_env.py prints a parseable table and exits 0 or 1
#  10  no print( in engine/repcut/**/*.py
#  11  nothing forbidden tracked by git
#  12  no regression: scripts/verify_00.sh still exits 0
#  13  build plan not transcribed into any tracked file (content, not filename)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Resolve a working python. Project venv first (that is where the engine is
# installed editable), then whatever is on PATH — `python3` can be a broken
# pyenv shim on Windows, so a candidate only counts if it actually executes.
PY=""
for c in .venv/Scripts/python.exe .venv/bin/python python3 python py; do
  case "$c" in
    */*) [ -x "$c" ] || continue ;;
    *)   command -v "$c" >/dev/null 2>&1 || continue ;;
  esac
  if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done

pass=0; fail=0
ok()   { printf "  [PASS] %-52s %s\n" "$1" "${2:-}"; pass=$((pass+1)); }
no()   { printf "  [FAIL] %-52s %s\n" "$1" "${2:-}"; fail=$((fail+1)); }
chk()  { if [ "$1" = 0 ]; then ok "$2" "${3:-}"; else no "$2" "${3:-}"; fi; }

# Never echo an absolute path that carries the OS username (secrets.md).
scrub() { sed -e 's#[A-Za-z]:[\\/][Uu]sers[\\/][^\\/ "]*#<HOME>#g' -e 's#/[Cc]/[Uu]sers/[^/ "]*#<HOME>#g' -e 's#/home/[^/ "]*#<HOME>#g'; }

TMPROOT="$(mktemp -d 2>/dev/null)" || TMPROOT=""
if [ -z "$TMPROOT" ]; then
  TMPROOT="${TMPDIR:-/tmp}/repcut-verify01-$$"
  mkdir -p "$TMPROOT" || TMPROOT=""
fi
LOG="${TMPROOT:-.}/engine.log"
ENGINE_PID=""

# Idempotent: safe to call when nothing is running, and safe to call twice
# (criterion 5 reuses it, and so does the EXIT trap).
stop_engine() {
  [ -n "$ENGINE_PID" ] || return 0
  if kill -0 "$ENGINE_PID" 2>/dev/null; then
    kill "$ENGINE_PID" 2>/dev/null
    i=0
    while [ "$i" -lt 20 ]; do
      kill -0 "$ENGINE_PID" 2>/dev/null || break
      sleep 0.25; i=$((i+1))
    done
    kill -9 "$ENGINE_PID" 2>/dev/null
  fi
  wait "$ENGINE_PID" 2>/dev/null
  ENGINE_PID=""
}

cleanup() {
  stop_engine
  if [ -n "$TMPROOT" ] && [ -d "$TMPROOT" ]; then rm -rf "$TMPROOT"; fi
  TMPROOT=""
}

# A signal trap that only cleans up is a trap that lies: bash resumes the script
# afterwards, so Ctrl-C would kill the engine mid-criterion and the run would
# carry on reporting fabricated failures. Clean up, then actually leave.
on_signal() { cleanup; trap - INT TERM EXIT; echo; echo "INTERRUPTED"; exit 130; }
trap cleanup EXIT
trap on_signal INT TERM

# Ephemeral port: the gate must be runnable while `make dev` holds :8000.
free_port() {
  "$PY" - <<'PYEOF'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PYEOF
}

# $1 = port, $2 = optional PYTHONPATH prefix (native form). Sets ENGINE_PID.
# 0 = ready, 1 = timed out, 2 = process died during boot.
boot_engine() {
  _port="$1"; _ppath="${2:-}"
  : > "$LOG"
  if [ -n "$_ppath" ]; then
    PYTHONPATH="$_ppath" "$PY" -m uvicorn repcut.main:app --host 127.0.0.1 \
      --port "$_port" --log-level warning >"$LOG" 2>&1 &
  else
    "$PY" -m uvicorn repcut.main:app --host 127.0.0.1 \
      --port "$_port" --log-level warning >"$LOG" 2>&1 &
  fi
  ENGINE_PID=$!
  _i=0
  while [ "$_i" -lt 90 ]; do            # 90 * 0.4s = 36s cap
    kill -0 "$ENGINE_PID" 2>/dev/null || return 2
    if "$PY" - "http://127.0.0.1:$_port/health" >/dev/null 2>&1 <<'PYEOF'
import http.client
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2.0) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError):
    sys.exit(1)
PYEOF
    then return 0; fi
    _i=$((_i+1)); sleep 0.4
  done
  return 1
}

# One short, CR-free, ANSI-free, path-scrubbed line from a log — never a dump.
log_detail() {
  [ -f "$1" ] || { printf 'no log'; return 0; }
  d="$(tr -d '\r' < "$1" | sed -e 's/\x1b\[[0-9;]*[a-zA-Z]//g' | grep -vE '^[[:space:]]*$' \
       | grep -iE 'error|failed|not recognized|cannot|no such|refused|traceback' | tail -1)"
  [ -n "$d" ] || d="$(tr -d '\r' < "$1" | grep -vE '^[[:space:]]*$' | tail -1)"
  printf '%s' "$d" | cut -c1-100 | scrub
}

# Hard cap around UI tooling. stdin is closed everywhere: `next lint` prompts
# interactively on a fresh repo and a watch-mode test runner never returns —
# either would hang the gate instead of failing it. Exit 124 == timed out.
TIMEOUT_BIN=""
command -v timeout >/dev/null 2>&1 && TIMEOUT_BIN=timeout
ui_run() {
  _secs="$1"; shift
  if [ -n "$TIMEOUT_BIN" ]; then
    ( cd ui && "$TIMEOUT_BIN" -k 10 "$_secs" "$@" ) </dev/null
  else
    ( cd ui && "$@" ) </dev/null
  fi
}

echo "verify-01 — engine + UI scaffold"

# ---------------------------------------------------------------- 1. scaffold
missing=""
for f in engine/pyproject.toml engine/repcut/main.py engine/repcut/config.py \
         ui/package.json ui/app/status/page.tsx scripts/check_env.py; do
  [ -f "$f" ] || missing="$missing $f"
done
[ -d engine/tests ] || missing="$missing engine/tests/"
[ -z "$missing" ]; chk $? "scaffold files present" "${missing:-(7/7)}"

# ------------------------------------------------- 2. ruff + ruff-format + mypy
if [ -z "$PY" ]; then
  no "python lint + types clean (ruff, format, mypy)" "(no working python found)"
else
  lintbad=""
  "$PY" -m ruff check engine >/dev/null 2>&1 || lintbad="$lintbad ruff-check"
  "$PY" -m ruff format --check engine >/dev/null 2>&1 || lintbad="$lintbad ruff-format"
  # Config file is explicit on purpose: run bare from the repo root, mypy misses
  # engine/pyproject.toml and passes without ever applying the strict settings.
  "$PY" -m mypy --config-file engine/pyproject.toml engine >/dev/null 2>&1 \
    || lintbad="$lintbad mypy"
  [ -z "$lintbad" ]; chk $? "python lint + types clean (ruff, format, mypy)" "${lintbad:-(3/3 exit 0)}"
fi

# ------------------------------------------------------------ 3. engine pytest
if [ -z "$PY" ]; then
  no "engine tests green (pytest -m 'not gpu')" "(no working python found)"
else
  pyt="$("$PY" -m pytest engine -m "not gpu" -q 2>&1)"; rc=$?
  npass="$(printf '%s\n' "$pyt" | grep -oE '[0-9]+ passed' | tail -1)"
  nfail="$(printf '%s\n' "$pyt" | grep -oE '[0-9]+ failed' | tail -1)"
  if [ "$rc" = 0 ] && [ -n "$npass" ]; then
    ok "engine tests green (pytest -m 'not gpu')" "($npass)"
  else
    no "engine tests green (pytest -m 'not gpu')" "(exit=$rc ${npass:-0 passed}${nfail:+, $nfail})"
  fi
fi

# ------------------------------------------------------------------ 4. /health
HEALTH_PY="${TMPROOT:-.}/health_check.py"
if [ -n "$TMPROOT" ]; then
  cat > "$HEALTH_PY" <<'PYEOF'
"""Validate GET /health. argv: <url> <expect_cpu:0|1>. Prints a measured line."""

import http.client
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
expect_cpu = sys.argv[2] == "1"

try:
    with urllib.request.urlopen(url, timeout=15.0) as resp:
        status = resp.status
        body = resp.read().decode("utf-8")
except urllib.error.HTTPError as exc:
    print("PROBLEM=http-%s" % exc.code)
    raise SystemExit(1)
except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as exc:
    print("PROBLEM=request-failed:%s" % type(exc).__name__)
    raise SystemExit(1)

if status != 200:
    print("PROBLEM=http-%d" % status)
    raise SystemExit(1)

try:
    data = json.loads(body)
except json.JSONDecodeError:
    print("PROBLEM=non-json-body")
    raise SystemExit(1)
if not isinstance(data, dict):
    print("PROBLEM=payload-not-an-object")
    raise SystemExit(1)


def a_bool(value: object) -> bool:
    return isinstance(value, bool)


def a_str(value: object) -> bool:
    return isinstance(value, str) and value != ""


def an_int(value: object) -> bool:
    # bool is a subclass of int in Python; a bool here would be a type error.
    return isinstance(value, int) and not isinstance(value, bool)


def optional(check):
    return lambda value: value is None or check(value)


SPEC = {
    "engine_version": a_str,
    "ffmpeg_version": optional(a_str),
    "ffmpeg_has_libx264": a_bool,
    "cuda_available": a_bool,
    "gpu_name": optional(a_str),
    "vram_free_mb": optional(an_int),
    "vram_total_mb": optional(an_int),
    "torch_device_active": lambda value: value in ("cuda", "cpu"),
    "data_dir_writable": a_bool,
    "gemini_api_key_set": a_bool,
}

missing = sorted(k for k in SPEC if k not in data)
bad = sorted(k for k in SPEC if k in data and not SPEC[k](data[k]))
device = data.get("torch_device_active")

# Never print gpu_name or any path — only the fields the gate reports on.
print("http=200 device=%s fields=%d/10" % (device, len(SPEC) - len(missing)))

problems = []
if missing:
    problems.append("missing:" + ",".join(missing))
if bad:
    problems.append("bad-type:" + ",".join(bad))
if expect_cpu:
    if data.get("cuda_available") is not False:
        problems.append("cuda_available=%r" % (data.get("cuda_available"),))
    if device != "cpu":
        problems.append("device=%r" % (device,))
if problems:
    print("PROBLEM=" + " ".join(problems))
    raise SystemExit(1)
raise SystemExit(0)
PYEOF
fi

C4="/health 200 with all 10 capability fields"
if [ -z "$PY" ]; then
  no "$C4" "(no working python found)"
elif [ -z "$TMPROOT" ]; then
  no "$C4" "(could not create a temp dir)"
else
  port="$(free_port)"
  if [ -z "$port" ]; then
    no "$C4" "(could not allocate an ephemeral port)"
  else
    boot_engine "$port"; brc=$?
    if [ "$brc" != 0 ]; then
      case $brc in
        2) reason="engine exited during boot" ;;
        *) reason="timed out after 36s" ;;
      esac
      no "$C4" "(port=$port $reason: $(log_detail "$LOG"))"
    else
      hout="$("$PY" "$HEALTH_PY" "http://127.0.0.1:$port/health" 0 2>&1)"; hrc=$?
      detail="$(printf '%s' "$hout" | tr '\n' ' ' | cut -c1-100)"
      chk $hrc "$C4" "(port=$port $detail)"
    fi
    stop_engine
  fi
fi

# ------------------------------------------------------- 5. CPU fallback proven
C5="CPU fallback: 200, cuda=false, device=cpu"
if [ -z "$PY" ] || [ -z "$TMPROOT" ]; then
  no "$C5" "(no working python or temp dir)"
else
  BLOCKDIR="$TMPROOT/torchblock"
  mkdir -p "$BLOCKDIR"
  cat > "$BLOCKDIR/sitecustomize.py" <<'PYEOF'
"""Throwaway: make torch unimportable so the engine's CPU path is exercised."""

import sys


class _TorchBlocker:
    """meta_path finder that refuses torch and every torch.* submodule."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "torch" or fullname.startswith("torch."):
            raise ImportError("torch blocked by verify-01 CPU-fallback probe")
        return None


sys.meta_path.insert(0, _TorchBlocker())
sys._repcut_torch_blocked = True  # marker: proves this file was actually loaded
PYEOF

  native_blockdir="$BLOCKDIR"; sep=":"
  if command -v cygpath >/dev/null 2>&1; then
    native_blockdir="$(cygpath -w "$BLOCKDIR")"; sep=";"
  fi
  ppath="$native_blockdir"
  [ -n "${PYTHONPATH:-}" ] && ppath="$native_blockdir$sep$PYTHONPATH"

  # Verify the fixture before trusting the result it produces: a sitecustomize
  # that never loads would let this criterion pass vacuously on a torch-less box.
  fixture_ok=0
  PYTHONPATH="$ppath" "$PY" -c "import sys; raise SystemExit(0 if getattr(sys, '_repcut_torch_blocked', False) else 1)" >/dev/null 2>&1 || fixture_ok=1
  PYTHONPATH="$ppath" "$PY" -c "import torch" >/dev/null 2>&1 && fixture_ok=1

  if [ "$fixture_ok" != 0 ]; then
    no "$C5" "(torch-blocker fixture did not load; not asserted)"
  else
    port5="$(free_port)"
    if [ -z "$port5" ]; then
      no "$C5" "(could not allocate an ephemeral port)"
    else
      boot_engine "$port5" "$ppath"; brc=$?
      if [ "$brc" != 0 ]; then
        case $brc in
          2) reason="engine exited during boot" ;;
          *) reason="timed out after 36s" ;;
        esac
        no "$C5" "(no-torch boot $reason: $(log_detail "$LOG"))"
      else
        hout5="$("$PY" "$HEALTH_PY" "http://127.0.0.1:$port5/health" 1 2>&1)"; hrc5=$?
        detail5="$(printf '%s' "$hout5" | tr '\n' ' ' | cut -c1-100)"
        chk $hrc5 "$C5" "(torch blocked, $detail5)"
      fi
      stop_engine
    fi
  fi
  rm -rf "$BLOCKDIR"
fi

# ------------------------------------------------------------------- 6. UI clean
C6="UI clean (tsc --noEmit, eslint, vitest)"
if [ ! -d ui/node_modules ]; then
  no "$C6" "(ui/node_modules missing — run make setup)"
else
  uibad=""
  ui_run 300 npx --no-install tsc --noEmit >/dev/null 2>&1; rc=$?
  [ "$rc" = 0 ] || uibad="$uibad tsc(exit=$rc)"
  ui_run 300 npm run lint --silent >/dev/null 2>&1; rc=$?
  [ "$rc" = 0 ] || uibad="$uibad lint(exit=$rc)"
  # CI=true keeps a vitest invocation in run-once mode even if the package
  # script forgets `run` — a watcher would hang the gate rather than fail it.
  CI=true ui_run 420 npm run test --silent >/dev/null 2>&1; rc=$?
  [ "$rc" = 0 ] || uibad="$uibad test(exit=$rc)"
  [ -z "$uibad" ]; chk $? "$C6" "${uibad:-(3/3 exit 0)}"
fi

# ------------------------------------------------------------------ 7. UI builds
C7="UI builds (next build)"
if [ ! -d ui/node_modules ]; then
  no "$C7" "(ui/node_modules missing — run make setup)"
else
  BUILDLOG="${TMPROOT:-.}/next-build.log"
  t0=$(date +%s)
  ui_run 900 npm run build >"$BUILDLOG" 2>&1; brc7=$?
  t1=$(date +%s)
  if [ "$brc7" = 0 ]; then
    ok "$C7" "($((t1-t0))s)"
  else
    # A failed build leaves a half-written .next that poisons the next run.
    # Nothing of value is lost: both `next dev` and `next build` regenerate it.
    rm -rf ui/.next
    no "$C7" "(exit=$brc7 after $((t1-t0))s: $(log_detail "$BUILDLOG"))"
    [ -f "$BUILDLOG" ] && [ -z "$TMPROOT" ] && rm -f "$BUILDLOG"
  fi
fi

# --------------------------------------------------------------- 8. no any in UI
anyhits="$(grep -rnE '(:[[:space:]]*any\b|\bas[[:space:]]+any\b)' \
  --include='*.ts' --include='*.tsx' \
  --exclude-dir=node_modules --exclude-dir=.next ui 2>/dev/null)"
anyfiles="$(printf '%s\n' "$anyhits" | grep -v '^$' | cut -d: -f1 | sort -u | tr '\n' ' ')"
[ -z "$anyfiles" ]; chk $? "no \`any\` in ui/**/*.{ts,tsx}" "${anyfiles:-(0 hits)}"

# ------------------------------------------------------------------ 9. check_env
C9="check_env.py table parses, exit 0 or 1"
if [ -z "$PY" ]; then
  no "$C9" "(no working python found)"
elif [ ! -f scripts/check_env.py ]; then
  no "$C9" "(scripts/check_env.py missing)"
else
  # Output is never echoed: it can mention the data dir and env-file state.
  cout="$("$PY" scripts/check_env.py 2>&1)"; crc=$?
  rows="$(printf '%s\n' "$cout" | grep -cE '\[ OK \]|\[WARN\]|\[FAIL\]')"
  summ="$(printf '%s\n' "$cout" | grep -cE '(PASSED|FAILED):')"
  # Exit 0 or 1 only — a traceback (exit 2+) is a failure. GPU and .env are not
  # required, so a WARN-heavy exit 1 is a legitimate pass here.
  { [ "$crc" = 0 ] || [ "$crc" = 1 ]; } && [ "$rows" -ge 1 ] && [ "$summ" -ge 1 ]
  chk $? "$C9" "(exit=$crc, rows=$rows, summary=$summ)"
fi

# ------------------------------------------------------- 10. no print( in engine
# Parsed, not grepped: the AST finds `print (x)` that a grep for `print(` misses,
# and does not fire on the word appearing inside a docstring or a comment.
# Reports repo-relative file:line, so no username-bearing path is ever printed.
if [ -n "$PY" ]; then
  prints="$("$PY" - <<'PYEOF' 2>/dev/null
import ast
import pathlib

bad = []
for path in sorted(pathlib.Path("engine/repcut").rglob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        bad.append("%s(syntax-error)" % path.as_posix())
        continue
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            bad.append("%s:%d" % (path.as_posix(), node.lineno))
print(" ".join(bad))
PYEOF
)"
else
  prints="$(grep -rlE '(^|[^A-Za-z0-9_.])print[[:space:]]*\(' --include='*.py' engine/repcut 2>/dev/null | tr '\n' ' ')"
fi
nfiles="$(find engine/repcut -name '*.py' 2>/dev/null | wc -l | tr -d ' ')"
[ -z "$prints" ]; chk $? "no print( call in engine/repcut" "${prints:-(0 calls, $nfiles files)}"

# ------------------------------------------------- 11. nothing forbidden tracked
tracked="$(git ls-files 2>/dev/null | grep -Ei \
  '(^|/)\.env$|(^|/)\.env\.(local|production|development)$|\.(mp4|mp3|mov|wav|mkv|webm|flac|m4a)$|\.(pt|onnx|pth|safetensors)$|(^|/)node_modules/|\.pdf$|\.docx$|Repcut_Prompt_Guide|Project_Instructions' \
  | tr '\n' ' ')"
[ -z "$tracked" ]; chk $? "no forbidden files tracked by git" "${tracked:-(clean)}"

# --------------------------------------------------------------- 12. verify-00
v0="$(bash scripts/verify_00.sh 2>&1)"; v0rc=$?
v0line="$(printf '%s\n' "$v0" | grep -E '^(PASSED|FAILED):' | tail -1)"
chk $v0rc "verify-00 still green (no regression)" "(${v0line:-no summary line})"

# ------------------------------------------- 13. build plan not transcribed in
# Criterion 11 matches FILENAMES. That is not sufficient: a source file that
# transcribes the plan is named something ordinary and passes it. This matches
# CONTENT.
#
# This criterion was green for three weeks over 305 lines of transcribed plan.
# Its first version looked for wave titles in the guide's own formatting
# (`Wave 2 — Magic Core`); engine/repcut/prompts_data.py stored the wave as
# "Wave 0" with the title in a separate field, so the pattern matched zero
# times. The guard was written against the shape of the SOURCE DOCUMENT rather
# than the shape of a LEAK, and a transcription is free to paraphrase and split
# fields apart. scripts/check_plan_leak.py now counts several
# formatting-independent signals - prompt entries, gate command lists, timeline
# estimates, report indexes - and still allows one or two hits as a quotation.
# See docs/guide-amendments/006-plan-not-in-public-repo.md.
if [ -z "$PY" ]; then
  no "build plan not transcribed into tracked files" "(no working python found)"
else
  leak_out="$("$PY" scripts/check_plan_leak.py 2>&1)"; leak_rc=$?
  leak_sum="$(printf '%s\n' "$leak_out" | head -1 | scrub)"
  chk $leak_rc "build plan not transcribed into tracked files" "($leak_sum)"
  if [ $leak_rc -ne 0 ]; then
    printf '%s\n' "$leak_out" | sed -n '2,12p' | scrub | sed 's/^/         /'
  fi
fi

# The torch/CUDA probe behind /health is GPU code, and GPU code never runs on a
# CI runner. This gate can only ever observe the device this machine has, so the
# local GPU run is part of the evidence, not an optional extra.
echo
echo "  NOTE: torch/CUDA probe is GPU code — CI has no GPU and never exercises it."
echo "        Run \`make test-gpu\` locally and record the result in docs/reports/prompt-01.md."

echo
if [ "$fail" -eq 0 ]; then
  echo "PASSED: $pass of $((pass+fail)) criteria"; exit 0
else
  echo "FAILED: $fail of $((pass+fail)) criteria"; exit 1
fi
