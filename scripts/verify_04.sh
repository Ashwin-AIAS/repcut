#!/usr/bin/env bash
# Gate for Prompt 04 — Phase A: the colour baseline (amendment 012).
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_03.sh;
# the measured value is printed beside every verdict.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Phase A success criteria (docs/prompts/run-prompt-04.md):
#   1  proxy colour, read from the file — HDR fixture proxy is bt709/tv/yuv420p,
#      mean luma in verify-03's band, and its error against the SDR reference
#      a stated fraction of the v1 proxy's error AND under an absolute bound;
#      SDR fixture unchanged from v1 within 2 luma codes
#   2  one normalisation — proxy and frame call the same function with the same
#      recipe object; changing the operator changes both argvs
#   3  short-side cap — portrait 720x1280, landscape 1280x720, no upscale
#   4  versions move together — proxy and scene versions above Prompt 03's,
#      derived from the tag; the fingerprint guards' negative controls fail
#   5  stale regeneration — a v1 clip opened gets v2 proxy + scenes; a second
#      open enqueues nothing; v1 files stay; duplicate upload enqueues nothing
#   6  someone can see it — a real browser on `make dev` draws the playing HDR
#      proxy; saturation and black level within tolerance of the SDR reference
#   7  no regression — scripts/verify_03.sh exits 0
#   8  [HUMAN] the Phase A boxes in docs/manual-checks/prompt-04.md are ticked
#
# Phase B criteria (9-18) are appended as Phase B is built — after STOP A.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Resolve a working python: project venv first (the engine is installed
# editable there), then PATH. A candidate only counts if it actually executes —
# `python3` is a broken pyenv shim on some Windows setups.
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
# A third verdict, for a criterion that is genuinely unrunnable right now (no
# implementation to test, or an environment that structurally cannot exercise
# it — see criterion 16's console check). Not a PASS: a criterion that prints
# PASS without executing is the failure the gate exists to prevent. Counted
# apart so the denominator never quietly shrinks (amendment 004 §3's
# reasoning, applied here the same way verify_02.sh applies it).
skipped() { printf "  [SKIP] %-46s %s\n" "$1" "${2:-}"; skip=$((skip+1)); }

# Never echo an absolute path carrying the OS username (secrets.md).
scrub() { sed -e 's#[A-Za-z]:[\\/][Uu]sers[\\/][^\\/ "]*#<HOME>#g' -e 's#/[Cc]/[Uu]sers/[^/ "]*#<HOME>#g' -e 's#/home/[^/ "]*#<HOME>#g'; }

# Run one measurement from verify_04_checks.py. Its MEASURED: line is printed
# beside the verdict, so every criterion shows the number it was judged on
# rather than only the judgement. Exit 2 from the checker is a SKIP, the same
# convention verify_01.sh's check_plan_titles.py already established.
CHECK_OUT=""
measure() {
  CHECK_OUT="$("$PY" scripts/verify_04_checks.py "$1" 2>&1)"
  rc=$?
  detail="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^MEASURED: ' | cut -c11- | scrub)"
  reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^FAILED: ' | cut -c9- | scrub)"
  skip_reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^SKIPPED: ' | cut -c10- | scrub)"
  if [ "$rc" != 0 ] && [ "$rc" != 2 ] && [ -z "$reason" ]; then
    # A crash rather than a verdict. Show the last real line so the failure is
    # actionable without dumping a traceback into the gate output.
    reason="$(printf '%s\n' "$CHECK_OUT" | grep -vE '^\s*$' | tail -1 | cut -c1-160 | scrub)"
  fi
  MEASURE_RC=$rc
  MEASURE_DETAIL="${detail:-(no measurement reported)}"
  MEASURE_REASON="$reason"
  MEASURE_SKIP="$skip_reason"
}

# $1 = check name, $2 = criterion label
criterion() {
  measure "$1"
  if [ "$MEASURE_RC" = 0 ]; then
    ok "$2" "$MEASURE_DETAIL"
  elif [ "$MEASURE_RC" = 2 ]; then
    skipped "$2" "${MEASURE_SKIP:-(no reason reported)}"
    [ -n "$MEASURE_DETAIL" ] && [ "$MEASURE_DETAIL" != "(no measurement reported)" ] && printf "         %s\n" "$MEASURE_DETAIL"
  else
    no "$2" "$MEASURE_DETAIL"
    [ -n "$MEASURE_REASON" ] && printf "         %s\n" "$MEASURE_REASON"
  fi
}

echo "verify-04 — colour baseline and grading engine"
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

# ------------------------------------------------------------ 1. proxy colour
criterion proxy-colour "1  HDR proxy colour, read from the file"

# ------------------------------------------------------- 2. one normalisation
criterion one-normalisation "2  one normalisation for proxy and frame"

# ------------------------------------------------------------ 3. short side
criterion short-side-cap "3  short side capped at 720, never upscaled"

# ------------------------------------------------------ 4. versions together
criterion versions-move-together "4  proxy + scene versions move together"

# ------------------------------------------------------ 5. stale regeneration
criterion stale-regeneration "5  stale clip regenerated lazily, once"

# ------------------------------------------------------ 6. someone can see it
# Slow, deliberately: a real `make dev`, a real browser drawing the product's
# own <video> into a canvas. Gemini is switched off for this stack.
criterion someone-can-see-it "6  a browser sees the HDR proxy as its SDR twin"

# ------------------------------------------------------------ 7. no regression
# Through the resolver, as `make verify-03` itself runs it — never a bare bash.
v3out="$("$PY" scripts/posix_shell.py scripts/verify_03.sh 2>&1)"; v3rc=$?
v3line="$(printf '%s
' "$v3out" | grep -E '^(PASSED|FAILED):' | tail -1)"
if [ $v3rc != 0 ]; then
  # Criterion 19 there is its own human checklist, signed at Prompt 03; a
  # failure anywhere else is printed so it is actionable from here.
  printf '%s
' "$v3out" | grep -E '^\s*\[FAIL\]' | scrub | sed 's/^/         /'
fi
chk $v3rc "7  verify-03 still green (no regression)" "(${v3line:-no summary line})"

# ----------------------------------------------------- 8. [HUMAN] Phase A boxes
MANUAL="docs/manual-checks/prompt-04.md"
phase_a() { awk '/^### Phase A/{on=1; next} /^### /{on=0} on' "$MANUAL"; }
if [ ! -f "$MANUAL" ]; then
  no "8  [HUMAN] Phase A boxes ticked" "($MANUAL does not exist)"
else
  unticked="$(phase_a | grep -cE '^[[:space:]]*-[[:space:]]*\[[[:space:]]\]' || true)"
  ticked="$(phase_a | grep -cE '^[[:space:]]*-[[:space:]]*\[[xX]\]' || true)"
  if [ "${unticked:-1}" = 0 ] && [ "${ticked:-0}" -gt 0 ]; then
    ok "8  [HUMAN] Phase A boxes ticked" "($ticked of $ticked)"
  else
    no "8  [HUMAN] Phase A boxes ticked" "($unticked unticked, $ticked ticked)"
    printf "         [HUMAN] colour baseline unverified — %s
" "$MANUAL"
  fi
fi

echo
echo "  NOTE: criteria 1-6 run against fixtures generated at test time, including"
echo "        a real 10-bit HLG encode of a lavfi pattern. No real footage is"
echo "        committed; criterion 8 is where real footage is signed off."

echo
skipnote=""
[ "$skip" -gt 0 ] && skipnote=" ($skip skipped, reason printed above)"
if [ "$fail" -eq 0 ]; then
  echo "PASSED: $pass of $((pass+fail)) criteria$skipnote"; exit 0
else
  echo "FAILED: $fail of $((pass+fail)) criteria$skipnote"; exit 1
fi
