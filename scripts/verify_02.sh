#!/usr/bin/env bash
# Gate for Prompt 02 — media pipeline (Track A) and, once Track B lands, the UI.
# Binary, exit-coded, per-criterion, idempotent. Same contract as verify_01.sh.
# See .claude/skills/verify-gate-authoring/SKILL.md
#
# Success criteria (one block each, in the guide's order):
#   1  migrations round-trip; six tables; fps columns; derived-artifact unique key
#   2  ffmpeg_builder snapshots green; no shell=True in engine/; no user path emitted
#   3  non-video rejected with a named error, no rows written
#   4  resumability across a real SIGKILL, offset from the server, idempotent
#   5  duplicate hash links: one blob, two references, no re-encode
#   6  VFR normalized to CFR, A/V drift < 40ms  (6b: unknown container stores NULL)
#   7  rotation metadata: stored resolution is the display resolution
#   8  ingest artifacts: strip cell count, 720p H.264 proxy, duration, audio rate
#   9  job lifecycle over /ws/jobs  (9b: a failure carries a cause, not a traceback)
#  10  UI typechecks, lints and builds  (tsc, eslint, next build)
#  11  tokens are the only source of style — no ad-hoc colour outside globals.css
#  12  accessibility baseline: vitest green, and an axe run in every component dir
#  13  large-file memory: 2GB upload, peak engine RSS < 500MB  (slow; may SKIP)
#  14  no regression: scripts/verify_01.sh still exits 0
#  15  nothing forbidden tracked by git
#  16  [HUMAN] docs/manual-checks/prompt-02.md has no unticked boxes
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Resolve a working python: project venv first (the engine is installed editable
# there), then PATH. A candidate only counts if it actually executes — `python3`
# is a broken pyenv shim on some Windows setups.
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
# A third verdict, and only where a criterion is *designed* to be unrunnable on
# some machines (criterion 13 needs 5GB free). It is not a PASS: a criterion
# that prints PASS without executing is the failure the gate exists to prevent
# (docs/reports/prompt-02.md, the sync-guard finding). It does not fail the gate
# either, per amendment 004 §3 - it prints, with the reason, and is counted
# apart so the denominator never quietly shrinks.
skipped() { printf "  [SKIP] %-46s %s\n" "$1" "${2:-}"; skip=$((skip+1)); }

# Never echo an absolute path carrying the OS username (secrets.md).
scrub() { sed -e 's#[A-Za-z]:[\\/][Uu]sers[\\/][^\\/ "]*#<HOME>#g' -e 's#/[Cc]/[Uu]sers/[^/ "]*#<HOME>#g' -e 's#/home/[^/ "]*#<HOME>#g'; }

# Run one measurement from verify_02_checks.py. Its MEASURED: line is printed
# beside the verdict, so every criterion shows the number it was judged on
# rather than only the judgement.
CHECK_OUT=""
measure() {
  CHECK_OUT="$("$PY" scripts/verify_02_checks.py "$1" 2>&1)"
  rc=$?
  detail="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^MEASURED: ' | cut -c11- | scrub)"
  reason="$(printf '%s\n' "$CHECK_OUT" | grep -m1 '^FAILED: ' | cut -c9- | scrub)"
  if [ "$rc" != 0 ] && [ -z "$reason" ]; then
    # A crash rather than a verdict. Show the last real line so the failure is
    # actionable without dumping a traceback into the gate output.
    reason="$(printf '%s\n' "$CHECK_OUT" | grep -vE '^\s*$' | tail -1 | cut -c1-110 | scrub)"
  fi
  MEASURE_RC=$rc
  MEASURE_DETAIL="${detail:-(no measurement reported)}"
  MEASURE_REASON="$reason"
}

# $1 = check name, $2 = criterion label
criterion() {
  measure "$1"
  if [ "$MEASURE_RC" = 0 ]; then
    ok "$2" "$MEASURE_DETAIL"
  else
    no "$2" "$MEASURE_DETAIL"
    [ -n "$MEASURE_REASON" ] && printf "         %s\n" "$MEASURE_REASON"
  fi
}

echo "verify-02 — media pipeline"
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

# ---------------------------------------------------------- 1. migrations
criterion migrations "1  migrations round-trip + schema"

# --------------------------------------------------- 2. builder snapshots
pytest_out="$("$PY" -m pytest engine/tests/test_ffmpeg_builder.py -q 2>&1)"; pyrc=$?
pytest_line="$(printf '%s\n' "$pytest_out" | grep -E '[0-9]+ (passed|failed)' | tail -1)"
chk $pyrc "2  ffmpeg_builder snapshots" "(${pytest_line:-no summary})"

# shell=True anywhere in the engine is a rule violation regardless of test
# results: `.claude/rules/ffmpeg.md` says arguments are a list[str], always.
#
# Parsed, not grepped. `ffmpeg_builder.py`'s own docstring contains the string
# "shell=True" in the sentence forbidding it, and a text search cannot tell that
# from a call - so the naive check failed on the file that implements the rule.
# The AST can only see a keyword argument actually being passed.
shell_hits="$("$PY" - <<'PYEOF'
import ast
import pathlib

offenders = []
for path in pathlib.Path("engine").rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "shell" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            ):
                offenders.append(f"{path.as_posix()}:{node.lineno}")
print("\n".join(offenders))
PYEOF
)"
shell_count="$(printf '%s' "$shell_hits" | grep -c . )"
[ "$shell_count" = 0 ]; chk $? "2  no shell=True in engine/" "($shell_count call sites)"
[ "$shell_count" != 0 ] && printf '%s\n' "$shell_hits" | sed 's/^/         /'

# A builder that emitted a path containing the OS username would leak it into
# every DEBUG log line. Asserted on the redactor's real output, not on intent.
"$PY" - <<'PYEOF' >/dev/null 2>&1
import sys
from pathlib import Path

sys.path.insert(0, "engine")
from repcut.media.ffmpeg_builder import build_proxy

command = build_proxy(
    Path("/home/someone/repcut-data/media/blobs/aa/source.mp4"),
    Path("/home/someone/repcut-data/media/derived/aa/proxy.mp4"),
    display_height=1080,
)
logged = " ".join(command.loggable_argv)
sys.exit(0 if "someone" not in logged and "Users" not in logged else 1)
PYEOF
chk $? "2  no user path in a logged invocation" "(loggable_argv redacted)"

# ------------------------------------------------------- 3. non-video rejected
criterion rejects-non-video "3  non-video rejected, no rows written"

# --------------------------------------------------------- 4. resumability
criterion resume "4  resume across a kill (idempotent)"

# ------------------------------------------------------- 5. duplicate hash
criterion duplicate "5  duplicate links, re-encodes nothing"

# ------------------------------------------------------ 6. VFR normalization
criterion vfr "6  VFR source -> CFR proxy, drift budget"
criterion vfr-unknown "6b unknown container stores NULL not false"

# ------------------------------------------------------------- 7. rotation
criterion rotation "7  stored resolution is display resolution"

# ---------------------------------------------------- 8. ingest artifacts
criterion artifacts "8  strip cells, proxy, duration, audio"

# ------------------------------------------------------- 9. job lifecycle
criterion lifecycle "9  /ws/jobs: queued -> running -> succeeded"
criterion failure-cause "9b failure carries a cause, not a traceback"

# ------------------------------------------------------- 10-13. Track B (UI)

# --- 10. the UI compiles, lints and builds -----------------------------------
# `next build` and not only `tsc`: the build is where a Server Component that
# imports a client-only module, or a `"use client"` boundary crossed by a
# server-only import, actually fails. Neither shows up in a typecheck.
if ! command -v npx >/dev/null 2>&1; then
  no "10 UI clean and builds" "(npx is not on PATH)"
else
  ui_fail=""
  for step in "typecheck:tsc --noEmit" "lint:eslint . --max-warnings 0"; do
    name="${step%%:*}"
    if ! (cd ui && npm run --silent "$name" >/tmp/repcut-ui-$name.log 2>&1); then
      ui_fail="$ui_fail $name"
    fi
  done
  if ! (cd ui && npm run --silent build >/tmp/repcut-ui-build.log 2>&1); then
    ui_fail="$ui_fail build"
  fi
  routes="$(grep -cE '^\s*[├└┌]' /tmp/repcut-ui-build.log 2>/dev/null || echo 0)"
  [ -z "$ui_fail" ]
  chk $? "10 UI clean and builds" "(tsc, eslint, next build; $routes routes)"
  [ -n "$ui_fail" ] && printf "         failed:%s — see /tmp/repcut-ui-*.log\n" "$ui_fail"
fi
# `npm run test` is the fourth command this criterion names; it is measured at
# criterion 12, which needs the same run, rather than executed twice here.
#
# `any` is checked separately because `tsc` does not: `strict` rejects an
# *implicit* any and says nothing about a written one, so `catch (e: any)`
# typechecks cleanly and defeats the rule (`.claude/rules/code-style.md`:
# use `unknown` and narrow). Matched in type position only, so `many`,
# `company` and prose are not hits.
any_hits="$(grep -rnE ':[[:space:]]*any\b|<[[:space:]]*any[[:space:]]*[,>]|,[[:space:]]*any[[:space:]]*>|\bas[[:space:]]+any\b|\bany\[\]' \
  ui/app ui/components ui/lib ui/test --include='*.ts' --include='*.tsx' 2>/dev/null | head -20)"
any_count="$(printf '%s' "$any_hits" | grep -c . )"
[ "$any_count" = 0 ]; chk $? "10 zero \`any\` in ui/" "($any_count occurrences)"
[ "$any_count" != 0 ] && printf '%s\n' "$any_hits" | sed 's/^/         /'

# --- 11. tokens are the only source of style ---------------------------------
# `globals.css` defines the tokens and `tailwind.config.ts` binds them to
# utilities; those two are the source and are exempt. A colour written anywhere
# else - a hex, an rgb()/hsl(), or a Tailwind arbitrary value - is a second
# source of style, and the next prompt inherits a component whose look depends
# on its call site rather than on the design system.
style_hits="$(grep -rnE '#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|-\[#' \
  ui/app ui/components --include='*.tsx' --include='*.ts' --include='*.css' 2>/dev/null \
  | grep -v '^ui/app/globals.css:' \
  | grep -viE '\.test\.tsx?:' \
  | head -20)"
style_count="$(printf '%s' "$style_hits" | grep -c . )"
[ "$style_count" = 0 ]; chk $? "11 tokens are the only source of style" "($style_count ad-hoc colours)"
[ "$style_count" != 0 ] && printf '%s\n' "$style_hits" | sed 's/^/         /'

# --- 12. accessibility baseline ----------------------------------------------
# Two halves, because either alone is hollow. The suite includes axe runs and
# the token contrast ratios recomputed from the parsed tokens; and every
# component directory must actually contain one of those axe runs, or a
# component added later is covered by a criterion that never looked at it.
vitest_out="$( (cd ui && npm run --silent test 2>&1) )"; vitest_rc=$?
vitest_line="$(printf '%s\n' "$vitest_out" | grep -E '^\s+Tests\s+' | tail -1 | tr -s ' ')"

uncovered=""
for dir in ui/components/*/; do
  ls "$dir"*.tsx >/dev/null 2>&1 || continue
  grep -rlq 'axe.run' "$dir" 2>/dev/null || uncovered="$uncovered $(basename "$dir")"
done

if [ "$vitest_rc" != 0 ]; then
  no "12 accessibility baseline" "(${vitest_line:-vitest failed})"
elif [ -n "$uncovered" ]; then
  no "12 accessibility baseline" "(no axe run in:$uncovered)"
else
  ok "12 accessibility baseline" "(${vitest_line:-vitest green}, axe in every component dir)"
fi

# --- 13. large-file memory ---------------------------------------------------
# Amendment 004 §3. Needs 5GB free and several minutes, so it is `slow`: it runs
# here and nowhere else. SKIPPED prints its reason and is counted apart - a
# memory budget nobody measured is a memory budget nobody has.
big_out="$("$PY" -m pytest engine/tests/test_large_upload.py -q -s -rs -m slow 2>&1)"; big_rc=$?
big_measured="$(printf '%s\n' "$big_out" | grep -m1 '^MEASURED: ' | cut -c11- | scrub)"
big_skip="$(printf '%s\n' "$big_out" | grep -m1 -oE 'SKIPPED \[[0-9]+\].*' | sed 's/.*: //' | scrub)"
if printf '%s\n' "$big_out" | grep -q '[0-9] skipped'; then
  skipped "13 large-file memory (2GB, RSS < 500MB)" "(${big_skip:-no reason reported})"
elif [ "$big_rc" = 0 ]; then
  ok "13 large-file memory (2GB, RSS < 500MB)" "(${big_measured:-no measurement reported})"
else
  no "13 large-file memory (2GB, RSS < 500MB)" "(${big_measured:-see output})"
  printf '%s\n' "$big_out" | grep -E '^E ' | head -3 | scrub | sed 's/^/         /'
fi

# --------------------------------------------------------- 14. no regression
v1="$(bash scripts/verify_01.sh 2>&1)"; v1rc=$?
v1line="$(printf '%s\n' "$v1" | grep -E '^(PASSED|FAILED):' | tail -1)"
chk $v1rc "14 verify-01 still green (no regression)" "(${v1line:-no summary line})"

# --------------------------------------------------- 15. nothing forbidden
# The guide's list, plus model weights and `data/`. `.gitkeep` is exempt and is
# the only exemption: it holds nothing, and it is what makes the default
# `DATA_DIR=./data` from `.env.example` resolve on a fresh clone. Any other
# tracked path under data/ is a media leak.
forbidden="$(git ls-files 2>/dev/null \
  | grep -iE '\.(mp4|mov|mkv|webm|hevc|m4v|wav|mp3|flac|m4a|aac|pt|pth|onnx|safetensors)$|^data/|(^|/)\.env$' \
  | grep -vE '^data/\.gitkeep$' | head -20)"
forbidden_count="$(printf '%s' "$forbidden" | grep -c . )"
[ "$forbidden_count" = 0 ]; chk $? "15 nothing forbidden tracked" "($forbidden_count files)"
[ "$forbidden_count" != 0 ] && printf '%s\n' "$forbidden" | sed 's/^/         /'

# ------------------------------------------------------ 16. [HUMAN] footage
MANUAL="docs/manual-checks/prompt-02.md"
if [ ! -f "$MANUAL" ]; then
  no "16 [HUMAN] real phone footage verified" "($MANUAL does not exist)"
else
  unticked="$(grep -cE '^[[:space:]]*-[[:space:]]*\[[[:space:]]\]' "$MANUAL" 2>/dev/null || true)"
  ticked="$(grep -cE '^[[:space:]]*-[[:space:]]*\[[xX]\]' "$MANUAL" 2>/dev/null || true)"
  if [ "${unticked:-1}" = 0 ] && [ "${ticked:-0}" -gt 0 ]; then
    ok "16 [HUMAN] real phone footage verified" "($ticked of $ticked boxes ticked)"
  else
    no "16 [HUMAN] real phone footage verified" "($unticked unticked, $ticked ticked)"
    printf "         [HUMAN] real phone footage unverified — %s\n" "$MANUAL"
  fi
fi

# The synthetic fixtures above prove the code handles synthetic VFR and a
# synthetic rotation tag. Only a person with a phone can prove it handles the
# real thing, and criterion 16 is the gate being honest about which is which
# rather than dropping the criterion (amendment 004 §1).
echo
echo "  NOTE: criteria 1-9 run against fixtures generated at test time. No real"
echo "        footage is committed; criterion 16 is where real footage is signed off."

echo
skipnote=""
[ "$skip" -gt 0 ] && skipnote=" ($skip skipped, reason printed above)"
if [ "$fail" -eq 0 ]; then
  echo "PASSED: $pass of $((pass+fail)) criteria$skipnote"; exit 0
else
  echo "FAILED: $fail of $((pass+fail)) criteria$skipnote"; exit 1
fi
