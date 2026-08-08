#!/usr/bin/env bash
# Gate for Prompt 02 — media pipeline (Track A) and, once Track B lands, the UI.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_01.sh.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (one block each, in the guide's order):
#   1  migrations round-trip; six tables; fps columns; derived-artifact unique key
#   2  ffmpeg_builder snapshots green; no shell=True in engine/; no user path emitted
#   3  non-video rejected with a named error, no rows written
#   4  resumability across a real SIGKILL, offset from the server, idempotent
#   5  duplicate hash links: one blob, two references, no re-encode
#   6  VFR normalized to CFR, A/V drift < 40ms  (6b: unknown container stores NULL)
#   7  rotation metadata: stored resolution is the display resolution
#   8  ingest artifacts: strip cell count, 720p H.264 proxy, duration, audio rate
#   9  job lifecycle over /ws/jobs  (9b: a failure carries a cause, not a traceback)
#  10-13 Track B (UI). Not yet built — reported as PENDING, and they fail the gate.
#  14  no regression: scripts/verify_01.sh still exits 0
#  15  nothing forbidden tracked by git
#  16  [HUMAN] docs/manual-checks/prompt-02.md has no unticked boxes
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Resolve a working python: project venv first (the engine is installed editable
# there), then PATH. A candidate only counts if it actually executes — `python3`
# is a broken pyenv shim on some Windows setups.
PY=""
for c in .venv/Scripts/python.exe .venv/bin/python python3 python py; do
  case "$c" in
    */*) [ -x "$c" ] || continue ;;
    *)   command -v "$c" >/dev/null 2>&1 || continue ;;
  esac
  if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done

pass=0; fail=0
ok()   { printf "  [PASS] %-46s %s\n" "$1" "${2:-}"; pass=$((pass+1)); }
no()   { printf "  [FAIL] %-46s %s\n" "$1" "${2:-}"; fail=$((fail+1)); }
chk()  { if [ "$1" = 0 ]; then ok "$2" "${3:-}"; else no "$2" "${3:-}"; fi; }

# Never echo an absolute path carrying the OS username (secrets.md).
scrub() { sed -e 's#[A-Za-z]:[\\/][Uu]sers[\\/][^\\/ "]*#<HOME>#g' -e 's#/[Cc]/[Uu]sers/[^/ "]*#<HOME>#g' -e 's#/home/[^/ "]*#<HOME>#g'; }

# Run one measurement from verify_02_checks.py. Its MEASURED: line is printed
# beside the verdict, so every criterion shows the number it was judged on
# rather than only the judgement.
CHECK_OUT=""
measure() {
  CHECK_OUT="$("$PY" scripts/verify_02_checks.py "$1" 2>&1)"
  rc=$?
  detail="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^MEASURED: ' | cut -c11- | scrub)"
  reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^FAILED: ' | cut -c9- | scrub)"
  if [ "$rc" != 0 ] && [ -z "$reason" ]; then
    # A crash rather than a verdict. Show the last real line so the failure is
    # actionable without dumping a traceback into the gate output.
    reason="$(printf '%s\n' "$CHECK_OUT" | grep -vE '^\s*$' | tail -1 | cut -c1-110 | scrub)"
  fi
  MEASURE_RC=$rc
  MEASURE_DETAIL="${detail:-(no measurement reported)}"
  MEASURE_REASON="$reason"
}

# $1 = check name, $2 = criterion label
criterion() {
  measure "$1"
  if [ "$MEASURE_RC" = 0 ]; then
    ok "$2" "$MEASURE_DETAIL"
  else
    no "$2" "$MEASURE_DETAIL"
    [ -n "$MEASURE_REASON" ] && printf "         %s\n" "$MEASURE_REASON"
  fi
}

echo "verify-02 — media pipeline"
echo

if [ -z "$PY" ]; then
  echo "  [FAIL] no working python found — run \`make setup\`"
  echo
  echo "FAILED: 1 of 1 criteria"
  exit 1
fi

for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "  [FAIL] $tool is not on PATH — every criterion below needs it"
    echo
    echo "FAILED: 1 of 1 criteria"
    exit 1
  }
done

# ---------------------------------------------------------- 1. migrations
criterion migrations "1  migrations round-trip + schema"

# --------------------------------------------------- 2. builder snapshots
pytest_out="$("$PY" -m pytest engine/tests/test_ffmpeg_builder.py -q 2>&1)"; pyrc=$?
pytest_line="$(printf '%s\n' "$pytest_out" | grep -E '[0-9]+ (passed|failed)' | tail -1)"
chk $pyrc "2  ffmpeg_builder snapshots" "(${pytest_line:-no summary})"

# shell=True anywhere in the engine is a rule violation regardless of test
# results: `.claude/rules/ffmpeg.md` says arguments are a list[str], always.
#
# Parsed, not grepped. `ffmpeg_builder.py`'s own docstring contains the string
# "shell=True" in the sentence forbidding it, and a text search cannot tell that
# from a call - so the naive check failed on the file that implements the rule.
# The AST can only see a keyword argument actually being passed.
shell_hits="$("$PY" - <<'PYEOF'
import ast
import pathlib

offenders = []
for path in pathlib.Path("engine").rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "shell" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            ):
                offenders.append(f"{path.as_posix()}:{node.lineno}")
print("\n".join(offenders))
PYEOF
)"
shell_count="$(printf '%s' "$shell_hits" | grep -c . )"
[ "$shell_count" = 0 ]; chk $? "2  no shell=True in engine/" "($shell_count call sites)"
[ "$shell_count" != 0 ] && printf '%s\n' "$shell_hits" | sed 's/^/         /'

# A builder that emitted a path containing the OS username would leak it into
# every DEBUG log line. Asserted on the redactor's real output, not on intent.
"$PY" - <<'PYEOF' >/dev/null 2>&1
import sys
from pathlib import Path

sys.path.insert(0, "engine")
from repcut.media.ffmpeg_builder import build_proxy

command = build_proxy(
    Path("/home/someone/repcut-data/media/blobs/aa/source.mp4"),
    Path("/home/someone/repcut-data/media/derived/aa/proxy.mp4"),
    display_height=1080,
)
logged = " ".join(command.loggable_argv)
sys.exit(0 if "someone" not in logged and "Users" not in logged else 1)
PYEOF
chk $? "2  no user path in a logged invocation" "(loggable_argv redacted)"

# ------------------------------------------------------- 3. non-video rejected
criterion rejects-non-video "3  non-video rejected, no rows written"

# --------------------------------------------------------- 4. resumability
criterion resume "4  resume across a kill (idempotent)"

# ------------------------------------------------------- 5. duplicate hash
criterion duplicate "5  duplicate links, re-encodes nothing"

# ------------------------------------------------------ 6. VFR normalization
criterion vfr "6  VFR source -> CFR proxy, drift budget"
criterion vfr-unknown "6b unknown container stores NULL not false"

# ------------------------------------------------------------- 7. rotation
criterion rotation "7  stored resolution is display resolution"

# ---------------------------------------------------- 8. ingest artifacts
criterion artifacts "8  strip cells, proxy, duration, audio"

# ------------------------------------------------------- 9. job lifecycle
criterion lifecycle "9  /ws/jobs: queued -> running -> succeeded"
criterion failure-cause "9b failure carries a cause, not a traceback"

# ------------------------------------------------------- 10-13. Track B (UI)
# Reported, not skipped. A criterion quietly omitted is a criterion nobody
# notices is missing (`.claude/rules/testing.md`); these fail until Track B
# lands, and the gate says why rather than printing a smaller denominator.
for pending in \
  "10 UI clean and builds" \
  "11 tokens are the only source of style" \
  "12 accessibility baseline" \
  "13 large-file memory (2GB, RSS < 500MB)"
do
  no "$pending" "(Track B not built yet)"
done

# --------------------------------------------------------- 14. no regression
v1="$(bash scripts/verify_01.sh 2>&1)"; v1rc=$?
v1line="$(printf '%s\n' "$v1" | grep -E '^(PASSED|FAILED):' | tail -1)"
chk $v1rc "14 verify-01 still green (no regression)" "(${v1line:-no summary line})"

# --------------------------------------------------- 15. nothing forbidden
# The guide's list, plus model weights and `data/`. `.gitkeep` is exempt and is
# the only exemption: it holds nothing, and it is what makes the default
# `DATA_DIR=./data` from `.env.example` resolve on a fresh clone. Any other
# tracked path under data/ is a media leak.
forbidden="$(git ls-files 2>/dev/null \
  | grep -iE '\.(mp4|mov|mkv|webm|hevc|m4v|wav|mp3|flac|m4a|aac|pt|pth|onnx|safetensors)$|^data/|(^|/)\.env$' \
  | grep -vE '^data/\.gitkeep$' | head -20)"
forbidden_count="$(printf '%s' "$forbidden" | grep -c . )"
[ "$forbidden_count" = 0 ]; chk $? "15 nothing forbidden tracked" "($forbidden_count files)"
[ "$forbidden_count" != 0 ] && printf '%s\n' "$forbidden" | sed 's/^/         /'

# ------------------------------------------------------ 16. [HUMAN] footage
MANUAL="docs/manual-checks/prompt-02.md"
if [ ! -f "$MANUAL" ]; then
  no "16 [HUMAN] real phone footage verified" "($MANUAL does not exist)"
else
  unticked="$(grep -cE '^[[:space:]]*-[[:space:]]*\[[[:space:]]\]' "$MANUAL" 2>/dev/null || true)"
  ticked="$(grep -cE '^[[:space:]]*-[[:space:]]*\[[xX]\]' "$MANUAL" 2>/dev/null || true)"
  if [ "${unticked:-1}" = 0 ] && [ "${ticked:-0}" -gt 0 ]; then
    ok "16 [HUMAN] real phone footage verified" "($ticked of $ticked boxes ticked)"
  else
    no "16 [HUMAN] real phone footage verified" "($unticked unticked, $ticked ticked)"
    printf "         [HUMAN] real phone footage unverified — %s\n" "$MANUAL"
  fi
fi

# The synthetic fixtures above prove the code handles synthetic VFR and a
# synthetic rotation tag. Only a person with a phone can prove it handles the
# real thing, and criterion 16 is the gate being honest about which is which
# rather than dropping the criterion (amendment 004 §1).
echo
echo "  NOTE: criteria 1-9 run against fixtures generated at test time. No real"
echo "        footage is committed; criterion 16 is where real footage is signed off."

echo
if [ "$fail" -eq 0 ]; then
  echo "PASSED: $pass of $((pass+fail)) criteria"; exit 0
else
  echo "FAILED: $fail of $((pass+fail)) criteria"; exit 1
fi
