#!/usr/bin/env bash
# Gate for Prompt 10 — the style profile, every platform, set captions.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_09.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  a generated moody, fast-cut taste history measurably shifts the
#      recommendations (theme ranking, pacing, intensity, caption style)
#   1b cold start (no events, or the profile off) == the edit from before
#      profiles, exactly
#   2  reframe: a walking subject's centre of mass stays in the middle third
#      of a reel for >= 95% of frames; nobody found -> the centre crop
#   3  on three labelled real hero segments, reps within one of the truth
#   3a three generated labelled sets within one rep; the boundary holds
#   3b both pinned YOLO models run on the GPU under 3.2 GB (NO_GPU skips)
#   4  export all platforms: 1080x1920, 1080x1080, 1920x1080, each through
#      Prompt 06's platform check
#   5  [HUMAN] profile-on is my style (docs/manual-checks/prompt-10.md)
#   6  security: the profile is read through a schema; no pickle, no eval
#   7  nothing leaves, nothing generated, no body judged (P1, P4)
#   8  switch, reset, confirm, dismiss: re-sync, logged, in the UI (P2, P3)
#   9  migration 0009 round-trips and matches the models
#   10 no regression: verify-09 (same mode) still green
#   11 the plan stays out (check_plan_leak.py)
#
# `--auto` (make verify-10-auto, amendment 016): criterion 5 is pending until
# Ashwin signs; criterion 3 is pending until the rep labels exist
# (gate_stop_bound, amendment 017). Clips are generated; `npm ci` in ui/ for 8.
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
  CHECK_OUT="$("$PY" scripts/verify_10_checks.py "$1" 2>&1)"
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

echo "verify-10 — the style profile, every platform, set captions"
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

criterion profile-shift    "1  taste history shifts recommendations"
criterion cold-start       "1b cold start == the edit before profiles"
criterion reframe          "2  walking subject: middle third >= 95%"

# --------------------------------------- 3. the labelled sets (human input)
measure reps-labelled
gate_stop_bound "3  real sets: reps within one of the label" "$MEASURE_RC" "$MEASURE_DETAIL"
criterion reps-generated   "3a generated sets within one rep; boundary"
criterion gpu-models       "3b YOLO detect + pose on GPU under 3.2 GB"
criterion platforms        "4  three platforms, each platform-safe"

# ------------------------------------------- 5. [HUMAN] profile-on is my style
criterion review-artifact  "5a the off/on review renders"
gate_human "5  [HUMAN] profile-on is my style (prompt-10.md)" "docs/manual-checks/prompt-10.md"

criterion schema-only      "6  profile read via schema; no pickle/eval"
criterion local-and-real   "7  nothing leaves, nothing generated (P1, P4)"
criterion overrides-and-ui "8  switch, reset, confirm: logged; UI (P2, P3)"
criterion migration        "9  migration 0009 round-trips"

# ----------------------------------------------------------- 10. no regression
v9out="$("$PY" scripts/posix_shell.py scripts/verify_09.sh $(gate_mode_arg) 2>&1)"; v9rc=$?
v9line="$(printf '%s\n' "$v9out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v9rc != 0 ]; then
  printf '%s\n' "$v9out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v9rc "10 verify-09 still green (no regression)" "(${v9line:-no summary line})"
gate_nested_skips "$v9out"

# ----------------------------------------------------------- 11. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "11 the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

echo
echo "  NOTE: 1-4 and 6-9 run on generated clips and generated taste histories. Whether"
echo "        the profile's edit feels like yours is criterion 5; whether the counter"
echo "        counts your reps is criterion 3, on segments you labelled."

echo
gate_finish verify-10
