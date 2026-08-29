#!/usr/bin/env bash
# Gate for Prompt 03 — analysis pipeline: scenes, one sampled frame per scene,
# Gemini scene understanding, motion and audio energy.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_02.sh.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Reconciliation pass: Track A and Track B have both landed, and every
# criterion below is written against the real, shipped API
# (`repcut.analysis.pipeline.run_analysis`, `.sampler.pick_frame`,
# `.scenes.detect_scenes`, `.motion.compute_scene_energy`, `.gemini_client`/
# `.cache.analyze_scene_cached`) rather than a first pass's guessed
# signatures. `main()`'s `ImportError` catch in `scripts/verify_03_checks.py`
# is kept as a safety net for a future rename, not the expected path.
#
# Success criteria (quoted verbatim from this session's plan, in order):
#   1  migrations round-trip; both new tables present (scenes,
#      gemini_scene_cache), with the cache's composite key
#   2  the sampled frame comes from the source — dimensions equal
#      media_blobs.display_width/display_height, not the proxy's or the
#      source's coded dimensions
#   3  exactly one frame per scene leaves the machine — N scenes -> N image
#      parts, no audio part, no filename/path in the request body
#   4  a repeat run costs zero API calls — cache hit per scene
#   5  prompt_version invalidates deliberately — bump it, calls come back
#   6  the limiter fails closed — bucket exhausted -> no request made
#   7  malformed JSON handled — garbage -> exactly one retry -> vlm: null
#   8  offline completes — connection error -> exit 0, vlm: null, UI warning
#   9  no key anywhere — GEMINI_API_KEY's value in no log/report/fixture/error
#      payload, and no absolute path containing the OS username
#  10  the frame carries no metadata — no EXIF, no GPS, no timed-metadata
#      stream, no side data beyond the picture
#  11  the frame is tone-mapped — BT.709 out, mean luma in a sane band
#  12  boundaries survive VFR — seconds against the source map to a source
#      frame within one frame duration
#  13  energy curves are not flat — per-scene energy varies by a stated
#      minimum across scenes
#  14  runtime budget — the guide's per-session budget, measured on synthetic
#      clips of equivalent total duration (may SKIP with a reason)
#  15  scripts/ is linted — ruff check scripts exits 0, no new UNJUSTIFIED
#      # noqa added (amendment 009 — a directive with a stated, checkable
#      reason passes; a bare one still fails). May SKIP if the tooling-fix
#      piece has not landed yet.
#  16  Ctrl-C is clean — make dev interrupted returns 130, no traceback (same
#      SKIP caveat as 15)
#  17  someone can start it and see the analysis — Playwright/CDP against a
#      real make dev stack: scene tags, an energy sparkline, the disclosure
#  18  no regression — scripts/verify_02.sh still exits 0
#  19  [HUMAN] docs/manual-checks/prompt-03.md has no unticked boxes
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

# Run one measurement from verify_03_checks.py. Its MEASURED: line is printed
# beside the verdict, so every criterion shows the number it was judged on
# rather than only the judgement. Exit 2 from the checker is a SKIP, the same
# convention verify_01.sh's check_plan_titles.py already established.
CHECK_OUT=""
measure() {
  CHECK_OUT="$("$PY" scripts/verify_03_checks.py "$1" 2>&1)"
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

echo "verify-03 — analysis pipeline"
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

# --------------------------------------------------------------- 1. migrations
criterion migrations "1  migrations round-trip; scenes + gemini_scene_cache"

# ------------------------------------------------------------- 2. frame source
criterion frame-source "2  sampled frame is the SOURCE's display dimensions"

# ------------------------------------------------------- 3. one frame per scene
criterion one-frame-per-scene "3  one image part per scene, no audio, no path"

# ------------------------------------------------------- 4. repeat run, cache
criterion repeat-run-zero-calls "4  repeat run costs zero API calls"

# ------------------------------------------------------------- 5. prompt_version
criterion prompt-version-invalidates "5  prompt_version bump invalidates the cache"

# ----------------------------------------------------------- 6. limiter closed
criterion limiter-fails-closed "6  limiter fails closed: bucket empty -> 0 calls"

# ------------------------------------------------------------- 7. malformed JSON
criterion malformed-json "7  malformed JSON -> one retry -> vlm: null"

# --------------------------------------------------------------- 8. offline
criterion offline-completes "8  offline completes: exit 0, vlm: null, warning"

# -------------------------------------------------------------- 9. no key leak
criterion no-key-leak "9  no key anywhere; no OS-username path either"

# ---------------------------------------------------------- 10. no metadata
criterion frame-no-metadata "10 sampled frame carries no EXIF/GPS/side data"

# --------------------------------------------------------- 11. tone-mapped
criterion frame-tone-mapped "11 sampled frame is tone-mapped to BT.709"

# ---------------------------------------------------------- 12. VFR boundaries
criterion boundaries-survive-vfr "12 boundaries survive VFR (<= 1 frame duration)"

# ------------------------------------------------------------- 13. energy curves
criterion energy-not-flat "13 energy curves are not flat across scenes"

# -------------------------------------------------------------- 14. runtime budget
criterion runtime-budget "14 runtime budget (amendment 008's guide figure)"

# ---------------------------------------------------------------- 15. scripts lint
criterion scripts-lint "15 scripts/ is linted; no new unjustified noqa"

# ------------------------------------------------------------------ 16. Ctrl-C
criterion ctrl-c-clean "16 Ctrl-C is clean: make dev returns 130"

# -------------------------------------------------------- 17. the assembled product
# Slow, deliberately: a real `make dev`, a real browser, a real upload. This is
# the criterion every prompt from here owes (docs/prompts/run-prompt-03.md) —
# the one that starts what a person starts and looks at what a person sees,
# rather than exercising a component in isolation.
criterion end-to-end-analysis "17 someone can start it and see the analysis"

# ------------------------------------------------------------- 18. no regression
v2out="$(bash scripts/verify_02.sh 2>&1)"; v2rc=$?
v2line="$(printf '%s\n' "$v2out" | grep -E '^(PASSED|FAILED):' | tail -1)"
chk $v2rc "18 verify-02 still green (no regression)" "(${v2line:-no summary line})"

# ------------------------------------------------------ 19. [HUMAN] real footage
# The automated criteria above run against fixtures generated at test time,
# including the HDR fixture (`lavfi` plus a colour tag, never a real clip) and
# the motion/loudness fixture — never real gym footage, per `.claude/rules/
# testing.md` and `docs/guide-amendments/004-...md` §1's split. Only a person
# with a phone can tell whether a boundary lands where the eye says the cut is,
# whether a scene tag actually describes the exercise, and whether the frame
# Gemini sees looks like the footage. This criterion is the gate being honest
# about which is which, rather than dropping the coverage silently.
MANUAL="docs/manual-checks/prompt-03.md"
if [ ! -f "$MANUAL" ]; then
  no "19 [HUMAN] real phone footage verified" "($MANUAL does not exist)"
else
  unticked="$(grep -cE '^[[:space:]]*-[[:space:]]*\[[[:space:]]\]' "$MANUAL" 2>/dev/null || true)"
  ticked="$(grep -cE '^[[:space:]]*-[[:space:]]*\[[xX]\]' "$MANUAL" 2>/dev/null || true)"
  if [ "${unticked:-1}" = 0 ] && [ "${ticked:-0}" -gt 0 ]; then
    ok "19 [HUMAN] real phone footage verified" "($ticked of $ticked boxes ticked)"
  else
    no "19 [HUMAN] real phone footage verified" "($unticked unticked, $ticked ticked)"
    printf "         [HUMAN] real phone footage unverified — %s\n" "$MANUAL"
  fi
fi

echo
echo "  NOTE: criteria 1-9 (and 11-14) run against synthetic fixtures generated at"
echo "        test time. No real footage is committed; criterion 19 is where real"
echo "        footage is signed off."
echo "  NOTE: criterion 16 needs a real console — it SKIPs in this sandboxed shell"
echo "        (GetConsoleWindow() == 0). Run \`make verify-03\` from cmd.exe or"
echo "        PowerShell to exercise it for real."

echo
skipnote=""
[ "$skip" -gt 0 ] && skipnote=" ($skip skipped, reason printed above)"
if [ "$fail" -eq 0 ]; then
  echo "PASSED: $pass of $((pass+fail)) criteria$skipnote"; exit 0
else
  echo "FAILED: $fail of $((pass+fail)) criteria$skipnote"; exit 1
fi
