#!/usr/bin/env bash
# Gate for Prompt 09 — the opening hook and the trust layer.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_08.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  on the real session Ashwin labelled once (verify-07's labels), the hook
#      is one of the top three hero moments
#   1a on a generated session, every hook candidate is a top-3 hero moment
#   2  with the hook on, the edit's length stays within 5% of its target
#   3  before/after is frame-accurate: the same frame on both sides, by frame
#      hash, at three positions (real proxy, real graded preview)
#   4  every claim in "What Repcut did" exists in the edit's plan JSON
#   5  hook on/off/choose re-sync and are logged (P2, P3); the candidate strip,
#      the slider and the summary panel do what they say
#   6  nothing new leaves the machine, nothing is generated (P1, P4)
#   7  migration 0008 round-trips and matches the models
#   8  no regression: verify-08 (same mode) still green
#   9  the plan stays out (check_plan_leak.py)
#
# No [HUMAN] criterion of its own. `--auto` (make verify-09-auto, amendment
# 016): criterion 1 is pending until the labels exist (gate_stop_bound,
# amendment 017). Clips are generated; `npm ci` in ui/ for criteria 3 and 5.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY=""
for c in .venv/Scripts/python.exe .venv/bin/python python3 python py; do
  case "$c" in
    */*) [ -x "$c" ] || continue ;;
    *)   command -v "$c" >/dev/null 2>&1 || continue ;;
  esac
  if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done

pass=0; fail=0; skip=0
ok()   { printf "  [PASS] %-46s %s\n" "$1" "${2:-}"; pass=$((pass+1)); }
no()   { printf "  [FAIL] %-46s %s\n" "$1" "${2:-}"; fail=$((fail+1)); }
chk()  { if [ "$1" = 0 ]; then ok "$2" "${3:-}"; else no "$2" "${3:-}"; fi; }
skipped() { printf "  [SKIP] %-46s %s\n" "$1" "${2:-}"; skip=$((skip+1)); }

# Never echo an absolute path carrying the OS username (secrets.md).
scrub() { sed -e 's#[A-Za-z]:[\\/][Uu]sers[\\/][^\\/ "]*#<HOME>#g' -e 's#/[Cc]/[Uu]sers/[^/ "]*#<HOME>#g' -e 's#/home/[^/ "]*#<HOME>#g'; }
. scripts/gate_skip.sh
. scripts/gate_human.sh
gate_mode "$@"

CHECK_OUT=""
measure() {
  CHECK_OUT="$("$PY" scripts/verify_09_checks.py "$1" 2>&1)"
  rc=$?
  detail="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^MEASURED: ' | cut -c11- | scrub)"
  reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^FAILED: ' | cut -c9- | scrub)"
  skip_reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^SKIPPED: ' | cut -c10- | scrub)"
  if [ "$rc" != 0 ] && [ "$rc" != 2 ] && [ "$rc" != 3 ] && [ -z "$reason" ]; then
    reason="$(printf '%s\n' "$CHECK_OUT" | grep -vE '^\s*$' | tail -1 | cut -c1-160 | scrub)"
  fi
  MEASURE_RC=$rc
  MEASURE_DETAIL="${detail:-(no measurement reported)}"
  MEASURE_REASON="$reason"
  MEASURE_SKIP="$skip_reason"
}

criterion() {
  measure "$1"
  if [ "$MEASURE_RC" = 0 ]; then
    ok "$2" "$MEASURE_DETAIL"
  elif [ "$MEASURE_RC" = 2 ] && [ -n "$MEASURE_SKIP" ]; then
    gate_skip "$2" "$MEASURE_SKIP"
  else
    no "$2" "$MEASURE_DETAIL"
    [ -n "$MEASURE_REASON" ] && printf "         %s\n" "$MEASURE_REASON"
    [ "$MEASURE_RC" = 2 ] && printf "         exit 2 with no SKIPPED: line - not a skip\n"
  fi
}

echo "verify-09 — the opening hook and the trust layer"
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

# ------------------------------------- 1. the labelled session (human input)
measure hook-labelled
gate_stop_bound "1  real session: the hook is a top-3 hero" "$MEASURE_RC" "$MEASURE_DETAIL"
criterion hook-synthetic   "1a generated session: candidates are top-3 heroes"
criterion duration         "2  hook on: length within 5% of target"
criterion frame-accurate   "3  before/after: same frame, 3 positions"
criterion summary-true     "4  every summary claim is in the plan JSON"
criterion overrides-and-ui "5  hook overrides re-sync, logged; UI (P2, P3)"
criterion local-and-real   "6  nothing leaves, nothing generated (P1, P4)"
criterion migration        "7  migration 0008 round-trips"

# ------------------------------------------------------------ 8. no regression
v8out="$("$PY" scripts/posix_shell.py scripts/verify_08.sh $(gate_mode_arg) 2>&1)"; v8rc=$?
v8line="$(printf '%s\n' "$v8out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v8rc != 0 ]; then
  printf '%s\n' "$v8out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v8rc "8  verify-08 still green (no regression)" "(${v8line:-no summary line})"
gate_nested_skips "$v8out"

# ------------------------------------------------------------ 9. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "9  the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

echo
echo "  NOTE: criteria 1a-7 run on generated clips. Whether the opening the AI picks"
echo "        is the one you would post first is criterion 1, on your labelled session."

echo
gate_finish verify-09
