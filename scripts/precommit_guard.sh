#!/usr/bin/env bash
# Local guardrail: blocks anything that must never reach a PUBLIC repository.
# Mirrors .github/workflows/gitleaks.yml so failures are caught before push.
set -uo pipefail
status=0

# The build plan is not published (CLAUDE.md). The filename rules below block it
# by NAME - `guide prompts.pdf`, `Repcut_Prompt_Guide*`. That is not enough: a
# source file that TRANSCRIBES the plan is called something else entirely and
# sails through. This happened - a `prompts_data.py` under engine/ carrying all
# 14 prompt entries passed every rule here, gitleaks, and verify-01.
#
# It then happened a SECOND time, to the fix. The content check that used to
# live here matched `Wave [0-5] - <title>` and its comment claimed "the
# transcription that slipped through matched 6". Measured against the real file:
# that regex matches it 0 times. The 6 is what a bare `Wave [0-5]` finds - a
# different pattern from the one that shipped. The guard was signed off against
# a number it never produced.
#
# So the content check no longer lives here as a regex. It is
# scripts/check_plan_leak.py, shared with `verify-01` criterion 13, tested
# against the real leaked file in engine/tests/test_plan_leak_guard.py. One
# implementation, one place to fix. See docs/guide-amendments/006.
PY=""
for c in .venv/Scripts/python.exe .venv/bin/python python3 python py; do
  command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
  "$c" -c "import sys" >/dev/null 2>&1 && { PY="$c"; break; }
done
plan_targets=""

for f in "$@"; do
  case "$f" in
    .env.example) continue ;;
    *.mp4|*.mov|*.mkv|*.avi|*.webm|*.m4v|*.mp3|*.wav|*.flac|*.aac|*.m4a|*.ogg)
      echo "BLOCKED (media, P4 + licensing): $f"; status=1 ;;
    *.pt|*.pth|*.onnx|*.safetensors)
      echo "BLOCKED (model weights, size): $f"; status=1 ;;
    *.pem|*.key|*.p12|*.pfx|*.jks)
      echo "BLOCKED (key material): $f"; status=1 ;;
    .env|.env.*)
      echo "BLOCKED (env file): $f"; status=1 ;;
    *Prompt_Guide*|*Project_Instructions*|Repcut_*|*.docx|*.doc|*.pptx)
      echo "BLOCKED (build plan / project doc, not published): $f"; status=1 ;;
    docs/reviews/*)
      case "$f" in
        docs/reviews/.gitkeep) ;;
        *) echo "BLOCKED (taste-review artifacts contain real footage stills, P4): $f"; status=1 ;;
      esac ;;
  esac

  # Collected, not scanned here: the plan check runs once over every staged
  # file below. Skipped for a path that is not a readable regular file -
  # pre-commit passes deletions, and verify_00 probes this guard with a
  # filename that does not exist.
  [ -f "$f" ] || continue
  plan_targets="$plan_targets $f"
done

# One pass over everything staged. A commit that transcribes the plan across
# several files is still a transcription.
if [ -n "$plan_targets" ]; then
  if [ -z "$PY" ]; then
    echo "BLOCKED (cannot check for build plan transcription: no working python)"
    status=1
  elif ! "$PY" scripts/check_plan_leak.py $plan_targets; then
    echo "        The plan lives outside this repo - see CLAUDE.md. Reference"
    echo "        prompts by number, not by copying their content. Quoting one"
    echo "        or two in docs/guide-amendments/ is fine."
    status=1
  fi
fi

if [ -f .env.example ] && grep -Eq '^[A-Z_]+=[[:space:]]*[A-Za-z0-9_-]{16,}' .env.example; then
  echo "BLOCKED: .env.example appears to contain a real value. Key names with EMPTY values only."
  status=1
fi

[ "$status" -ne 0 ] && echo "--- Nothing above may be committed. This repository is public. ---"
exit "$status"
