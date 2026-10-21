#!/usr/bin/env bash
# Gate for Prompt 11 — talk to the timeline: the copilot over the operation registry.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_10.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  the 20-intent eval, replayed: >= 90% correct call sequences; both
#      ambiguous intents ask; both natural-only ones refuse naming the
#      principle; the impossible one lands in copilot_unmet_requests
#   1L the same eval against the real model (needs the laptop's key)
#   2  every copilot mutation + undo restores byte-identical plan state, for
#      every registry operation
#   3  set_theme by button and by chat: identical plan, identical taste event
#      apart from its source
#   4  offline / no key: chat disabled with its reason; every control works
#   5  security: a fixed allowlist with validated arguments; a frame's
#      instruction to call an unlisted operation changes nothing and is logged
#   6  natural-only refusals with or without the model; logged (P1, P3)
#   7  a compact summary (< 1.5k tokens), no path; no network but the shared
#      client (P4)
#   8  the controls' routes run the registry's operations, one undo stack
#   9  migration 0010 round-trips and matches the models
#   10 no regression: verify-10 (same mode) still green
#   11 the plan stays out (check_plan_leak.py)
#
# No [HUMAN] criterion. `--auto` (make verify-11-auto, amendment 016):
# criterion 1L is pending until it is run on the laptop with
# REPCUT_COPILOT_LIVE=1 (gate_stop_bound, amendment 017). `npm ci` in ui/ for 4.
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
  CHECK_OUT="$("$PY" scripts/verify_11_checks.py "$1" 2>&1)"
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

echo "verify-11 — talk to the timeline: the copilot over the operation registry"
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

criterion intent-eval       "1  20-intent eval (replay) >= 90%"
measure intent-eval-live
gate_stop_bound "1L 20-intent eval, real model >= 90%" "$MEASURE_RC" "$MEASURE_DETAIL"
criterion undo              "2  mutation + undo is byte-identical"
criterion equivalence       "3  button == chat (plan, taste event)"
criterion offline           "4  offline: chat off, controls fine (P5)"
criterion allowlist         "5  allowlist, strict args, injection"
criterion natural-only      "6  natural-only refusals, logged (P1, P3)"
criterion local-and-compact "7  compact summary, nothing leaves (P4)"
criterion one-stack         "8  controls run the registry, one stack"
criterion migration         "9  migration 0010 round-trips"

# ----------------------------------------------------------- 10. no regression
v10out="$("$PY" scripts/posix_shell.py scripts/verify_10.sh $(gate_mode_arg) 2>&1)"; v10rc=$?
v10line="$(printf '%s\n' "$v10out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v10rc != 0 ]; then
  printf '%s\n' "$v10out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v10rc "10 verify-10 still green (no regression)" "(${v10line:-no summary line})"
gate_nested_skips "$v10out"

# ----------------------------------------------------------- 11. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "11 the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

echo
echo "  NOTE: the model is scripted in every criterion but 1L. Whether the real model"
echo "        picks the right calls is 1L, run on the laptop with its own key."

echo
gate_finish verify-11
