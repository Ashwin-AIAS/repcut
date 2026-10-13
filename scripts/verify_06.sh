#!/usr/bin/env bash
# Gate for Prompt 06 — captions and the export: the end of the first wave.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_05.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1  end to end: three generated clips in, a 9:16 MP4 out, zero manual steps
#      (upload, analysis, Moody, a generated track, the edit, local captions,
#      the Reel export), exit 0
#   2  the exported file, by ffprobe: H.264 yuv420p 1080x1920, bt709 tv,
#      AAC 48 kHz, moov before mdat (faststart), duration within 5 %
#   3  captions: known English and German speech, word error rate under 15 %
#   4  a music-only section produces zero captions
#   5  the render killed at 50 % restarts and finishes without re-rendering
#      the shots that were finished
#   6  render budget: 60 s of 1080p graded and captioned in under 5 minutes
#   7  security: caption text with quotes, backslashes and <script> is escaped
#      at both sinks - the subtitle filter (checked on pixels) and the DOM
#   8  caption and export changes are validated and logged as taste events
#   9  migration 0005 round-trips and matches the models
#  10  no regression: verify-05 (same mode) still green
#  11  the plan stays out (check_plan_leak.py)
#  12  [HUMAN] Wave 1: every box in docs/manual-checks/prompt-06.md is ticked
#
# `--auto` (make verify-06-auto, amendment 016): every criterion but 12, which
# is printed as pending until Ashwin signs it.
#
# Needs, once: `python scripts/fetch_models.py --gate` (the speech-to-text model
# and two test voices) and `npm ci` in ui/. Without them criteria 1-4 and 7
# FAIL with that instruction: a missing model is not an amendment-014 skip.
#
# Built with STOP A and STOP B unsigned (amendment 016): the export grades with
# the PROVISIONAL tone-map operator (hable) and the unjudged themes.
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
  CHECK_OUT="$("$PY" scripts/verify_06_checks.py "$1" 2>&1)"
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

echo "verify-06 — captions and the export"
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

criterion e2e               "1  3 clips in, 9:16 MP4 out, no manual step"
criterion platform-safe     "2  yuv420p, faststart, AAC 48k, size, length"
criterion caption-accuracy  "3  known speech: WER under 15 % (en, de)"
criterion music-only        "4  music only: zero captions"
criterion resume-after-kill "5  killed at 50 %, resumes, no re-render"
criterion speed-budget      "6  60 s of 1080p exported in under 5 min"
criterion escaping          "7  caption text escaped: filter and DOM"
criterion api-and-taste     "8  changes validated, logged as taste"
criterion migration         "9  migration 0005 round-trips"

# ----------------------------------------------------------- 10. no regression
v5out="$("$PY" scripts/posix_shell.py scripts/verify_05.sh $(gate_mode_arg) 2>&1)"; v5rc=$?
v5line="$(printf '%s\n' "$v5out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v5rc != 0 ]; then
  printf '%s\n' "$v5out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v5rc "10 verify-05 still green (no regression)" "(${v5line:-no summary line})"
gate_nested_skips "$v5out"

# ------------------------------------------------------------ 11. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "11 the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

# ------------------------------------- 12. [HUMAN] Wave 1: every box ticked
gate_human "12 [HUMAN] every box in prompt-06.md ticked" "docs/manual-checks/prompt-06.md"

echo
echo "  NOTE: criteria 1-9 run on generated clips, generated music and synthesised"
echo "        speech. Criterion 12 is the Wave 1 decision: your own session,"
echo "        exported, and whether you would post it."

echo
gate_finish verify-06
