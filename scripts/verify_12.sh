#!/usr/bin/env bash
# Gate for Prompt 12 — the quality harness: tests, the eval, the standing suites.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_11.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (the guide's, in our words, plus what this build adds):
#   1a the whole CPU engine suite green, run shuffled (order-independent), with
#      >= 80% line coverage on edit, grading and analysis
#   1b vitest, tsc and eslint green in ui/
#   1c the browser happy path (theme, edit, copilot off, export) on make dev
#   2  make eval on the real fixtures: every claim's gate PASS, scorecard written
#   2b the fixtures: real, cut locally, < 100 MiB, out of git, hash-only manifest
#   2c the eval's verdict logic on known answers (BOUND never PASS)
#   3  a deliberately broken beat-sync fails the beat gate (mutation)
#   3b CI shape: unit + vitest on PRs, no GPU, coverage floors, audits
#   3c CI under 10 minutes (the newest run on GitHub)
#   4  security: a regression test for every finding to date; audits gate the build
#   5  Gemini mocked in every test; the one live call is opt-in
#   6  orphan collection leaves zero unreferenced bytes (owed since amendment 004)
#   7  ruff and mypy pinned and clean
#   8  no regression: verify-11 (same mode) still green
#   9  the plan stays out (check_plan_leak.py)
#
# No [HUMAN] criterion. `--auto` (make verify-12-auto, amendment 016):
# criteria 2, 2b and 3c are pending until the fixtures and labels exist and a
# PR has run CI (gate_stop_bound, amendment 017). 1c needs Chrome or Edge and
# `make dev`; `npm ci` in ui/ for 1b and 4.
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
  CHECK_OUT="$("$PY" scripts/verify_12_checks.py "$1" 2>&1)"
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

echo "verify-12 — the quality harness: tests, the eval, the standing suites"
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

criterion engine-suite   "1a engine suite, shuffled; coverage >= 80%"
criterion ui-suite       "1b vitest, tsc, eslint"
criterion browser-e2e    "1c browser: theme, edit, copilot off, export"
measure eval-scorecard
gate_stop_bound "2  make eval: every claim PASS, scorecard" "$MEASURE_RC" "$MEASURE_DETAIL"
measure fixtures
gate_stop_bound "2b fixtures real, local, < 100 MiB" "$MEASURE_RC" "$MEASURE_DETAIL"
criterion eval-honesty   "2c verdict logic; BOUND is never PASS"
criterion mutation       "3  broken beat-sync fails the eval gate"
criterion ci-shape       "3b CI: unit + vitest on PR, no GPU, floors"
measure ci-time
gate_stop_bound "3c CI under 10 minutes" "$MEASURE_RC" "$MEASURE_DETAIL"
criterion security       "4  a regression test for every finding"
criterion gemini-mocked  "5  Gemini mocked everywhere; live is opt-in"
criterion gc             "6  orphan collection: zero unreferenced"
criterion lint           "7  ruff + mypy pinned and clean"

# ----------------------------------------------------------- 8. no regression
v11out="$("$PY" scripts/posix_shell.py scripts/verify_11.sh $(gate_mode_arg) 2>&1)"; v11rc=$?
v11line="$(printf '%s\n' "$v11out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v11rc != 0 ]; then
  printf '%s\n' "$v11out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v11rc "8  verify-11 still green (no regression)" "(${v11line:-no summary line})"
gate_nested_skips "$v11out"

# ----------------------------------------------------------- 9. plan stays out
leak="$("$PY" scripts/check_plan_leak.py 2>&1)"; leakrc=$?
chk $leakrc "9  the plan stays out" "($(printf '%s\n' "$leak" | tail -1 | scrub))"

echo
echo "  NOTE: synthetic clips never stand in for a claim: criterion 2 measures the"
echo "        real fixtures or stays pending. Criterion 3 uses generated clips on"
echo "        purpose - it tests the gate, not the product."

echo
gate_finish verify-12
