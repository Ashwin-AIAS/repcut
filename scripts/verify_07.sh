#!/usr/bin/env bash
# Gate for Prompt 07 — hero moments and slow motion.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_06.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  on a real session Ashwin labelled once, the top three moments include
#      the heaviest lift (engine/tests/fixtures/hero_labels.json)
#   1a on a generated session, the hardest set's last reps rank first, with reasons
#   2  RIFE 2x on a 3 s 1080p segment in under 60 s on the GPU, no OOM,
#      peak VRAM printed
#   2b the CPU path runs the verified weights at 1080p (mandatory fallback)
#   3  a fast barbell pan is refused: normal speed, the reason logged, exit 0
#   4  an export with an accepted slow shot passes Prompt 06's platform checks
#   5  security: the weights load from a pinned path after a digest check,
#      weights-only; no other torch.load anywhere
#   6  inherited from Prompt 01 (amendment 003): with CUDA torch, /health
#      reports the GPU, its name and its VRAM
#   7  2x is a hard ceiling: no speed, setting or stored plan exceeds it (P1)
#   8  heroes become drop-aligned slow cuts; the panel badges them with score
#      and reason, the per-cut toggle re-syncs and is logged as taste (P2, P3)
#   9  migration 0006 round-trips and matches the models
#  10  no regression: verify-06 (same mode) still green
#  11  the plan stays out (check_plan_leak.py)
#  12  [HUMAN] every box in docs/manual-checks/prompt-07.md is ticked
#
# `--auto` (make verify-07-auto, amendment 016): criterion 1 is pending until
# the labels exist (gate_stop_bound, amendment 017), 12 until Ashwin signs.
# 2 and 6 SKIP as NO_GPU where no GPU is visible (amendment 014).
#
# Needs, once: torch (`make setup-gpu`) and `python scripts/fetch_models.py`
# (the RIFE weights). Without them 2b, 3 and 4 FAIL with that instruction: a
# missing model is not an amendment-014 skip. `npm ci` in ui/ for criterion 8.
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
  CHECK_OUT="$("$PY" scripts/verify_07_checks.py "$1" 2>&1)"
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

echo "verify-07 — hero moments and slow motion"
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
measure hero-labelled
gate_stop_bound "1  real session: heaviest lift in the top 3" "$MEASURE_RC" "$MEASURE_DETAIL"
criterion hero-synthetic     "1a generated session: hardest set ranks first"
criterion rife-gpu-speed     "2  RIFE 2x, 3 s of 1080p under 60 s on the GPU"
criterion rife-cpu           "2b the CPU path runs the verified weights"
criterion barbell-reverts    "3  fast barbell pan: refused, reason logged"
criterion export-with-slowmo "4  export with slow motion is platform-safe"
criterion weights-security   "5  weights pinned, checked, weights-only"
criterion health-cuda        "6  /health reports the GPU (amendment 003)"
criterion ceiling            "7  2x is a hard ceiling (P1)"
criterion planner-and-ui     "8  drop-aligned slow cuts, badges, toggle"
criterion migration          "9  migration 0006 round-trips"

# ----------------------------------------------------------- 10. no regression
v6out="$("$PY" scripts/posix_shell.py scripts/verify_06.sh $(gate_mode_arg) 2>&1)"; v6rc=$?
v6line="$(printf '%s\n' "$v6out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v6rc != 0 ]; then
  printf '%s\n' "$v6out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v6rc "10 verify-06 still green (no regression)" "(${v6line:-no summary line})"
gate_nested_skips "$v6out"

# ------------------------------------------------------------ 11. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "11 the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

# --------------------------------- 12. [HUMAN] the slow motion, on real footage
gate_human "12 [HUMAN] every box in prompt-07.md ticked" "docs/manual-checks/prompt-07.md"

echo
echo "  NOTE: criteria 1a-9 run on generated clips. The confidence threshold was"
echo "        set on generated motion; criterion 12 is where it meets your footage."

echo
gate_finish verify-07
