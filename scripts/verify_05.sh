#!/usr/bin/env bash
# Gate for Prompt 05 — the auto-edit: music library, usable footage, the cut
# planner, re-sync on overrides, ducking.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_04.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  planner: every cut on a beat (+-40 ms of the detected grid and of the
#      generated truth), duration within 5 %, min shot length, deterministic by
#      seed - at 15, 30 and 60 s
#   2  a new track re-snaps 100 % of unlocked cuts; locked shots byte-identical
#   3  the drop opens on a cut within one bar, with top-quartile-energy footage
#   4  a full re-plan of a large session with locks stays under 3 s
#   5  ducking: speech segments >= 8 dB lower in rendered audio
#   6  the pinned speech model is fetched, verified, and silent on music/noise/tone
#   7  empty music library -> a valid silent-mode edit through the real engine
#   8  every override re-syncs and writes one taste event; decisions persist
#   9  security: tracks decode only through the validated source; no path
#      component derives from a track's name
#  10  migration 0004 round-trips and matches the models
#  11  someone can edit: a real browser on `make dev` swaps the track, locks and
#      removes a cut, and the timeline re-syncs
#  12  no regression: verify-04 (same mode) still green
#  13  the plan stays out (check_plan_leak.py)
#  14  [HUMAN] every box in docs/manual-checks/prompt-05.md is ticked
#
# `--auto` (make verify-05-auto, amendment 016): every criterion but 14, which
# is printed as pending until Ashwin signs it.
#
# Built with STOP A and STOP B unsigned (amendment 016): footage is measured on
# proxies normalised with the PROVISIONAL tone-map operator (hable).
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
  CHECK_OUT="$("$PY" scripts/verify_05_checks.py "$1" 2>&1)"
  rc=$?
  detail="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^MEASURED: ' | cut -c11- | scrub)"
  reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^FAILED: ' | cut -c9- | scrub)"
  skip_reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^SKIPPED: ' | cut -c10- | scrub)"
  if [ "$rc" != 0 ] && [ "$rc" != 2 ] && [ -z "$reason" ]; then
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

echo "verify-05 — the auto-edit"
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

criterion planner-on-beat     "1  cuts on beats, duration, min shot, seed"
criterion track-change-resync "2  new track re-snaps; locked shots identical"
criterion drop-alignment      "3  drop opens on a cut with top footage"
criterion resync-budget       "4  full re-plan under 3 s"
criterion ducking             "5  >= 8 dB music reduction under speech"
criterion speech-model        "6  speech model pinned, verified, no false hits"
criterion empty-library       "7  empty library -> silent-mode edit"
criterion overrides-logged    "8  overrides re-sync and are taste events"
criterion music-security      "9  validated source; no path from track names"
criterion migration           "10 migration 0004 round-trips"
# Slow, deliberately: a real `make dev` and a real browser using the edit screen.
criterion someone-can-edit    "11 a browser edits; the timeline re-syncs"

# ----------------------------------------------------------- 12. no regression
v4out="$("$PY" scripts/posix_shell.py scripts/verify_04.sh $(gate_mode_arg) 2>&1)"; v4rc=$?
v4line="$(printf '%s\n' "$v4out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v4rc != 0 ]; then
  printf '%s\n' "$v4out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v4rc "12 verify-04 still green (no regression)" "(${v4line:-no summary line})"
gate_nested_skips "$v4out"

# ------------------------------------------------------------ 13. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "13 the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

# --------------------------------------------- 14. [HUMAN] every box ticked
gate_human "14 [HUMAN] every box in prompt-05.md ticked" "docs/manual-checks/prompt-05.md"

echo
echo "  NOTE: every criterion above runs on generated footage and generated music."
echo "        No real footage or track is committed; criterion 14 is where the"
echo "        edit made from your own session is judged."

echo
gate_finish verify-05
