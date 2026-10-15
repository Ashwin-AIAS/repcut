#!/usr/bin/env bash
# Gate for Prompt 08 — matching a reference clip.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_07.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  round trip: a clip graded with Moody, its look extracted, lands within
#      tolerance of Moody's own numbers (and the tolerance excludes Hardstyle)
#   2  a fast-cut and a slow cinematic reference give measurably different
#      planner numbers
#   3  a saved reference look is in the theme selector and survives a restart
#   4  [HUMAN] mine feels like theirs (docs/manual-checks/prompt-08.md)
#   5  security: the reference is hostile input - the same probe, ceiling and
#      refusals as any upload; no route fetches a video
#   6  P1: the look transfer is capped statistical colour mapping, no model
#   7  one click applies look, pacing and captions; each detaches alone, puts
#      the person's choice back and is logged (P2, P3); the panel does it
#   8  migration 0007 round-trips and matches the models
#   9  no regression: verify-07 (same mode) still green
#  10  the plan stays out (check_plan_leak.py)
#
# `--auto` (make verify-08-auto, amendment 016): criterion 4 is pending until
# Ashwin signs it. Clips are generated (scripts/reference_fixture.py): no
# footage, nothing downloaded. `npm ci` in ui/ for criterion 7.
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
  CHECK_OUT="$("$PY" scripts/verify_08_checks.py "$1" 2>&1)"
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

echo "verify-08 — matching a reference clip"
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

criterion moody-round-trip     "1  Moody round trip: extracted look in tolerance"
criterion pacing-differs       "2  fast vs slow reference: different pacing"
criterion saved-themes-persist "3  saved look in the selector, after a restart"

# ------------------------------------------- 4. [HUMAN] mine feels like theirs
gate_human "4  [HUMAN] mine feels like theirs (prompt-08.md)" "docs/manual-checks/prompt-08.md"

criterion hostile-reference    "5  reference = hostile input, upload refusals"
criterion statistical-only     "6  statistical colour mapping only, capped (P1)"
criterion match-and-ui         "7  match, detach each part, restore, log (P2, P3)"
criterion migration            "8  migration 0007 round-trips"

# ------------------------------------------------------------ 9. no regression
v7out="$("$PY" scripts/posix_shell.py scripts/verify_07.sh $(gate_mode_arg) 2>&1)"; v7rc=$?
v7line="$(printf '%s\n' "$v7out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v7rc != 0 ]; then
  printf '%s\n' "$v7out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v7rc "9  verify-07 still green (no regression)" "(${v7line:-no summary line})"
gate_nested_skips "$v7out"

# ------------------------------------------------------------ 10. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "10 the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

echo
echo "  NOTE: criteria 1-3 and 5-8 run on generated clips. Whether your footage"
echo "        feels like the reference is criterion 4, and only you can say."

echo
gate_finish verify-08
