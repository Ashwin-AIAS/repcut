#!/usr/bin/env bash
# Local guardrail: blocks anything that must never reach a PUBLIC repository.
# Mirrors .github/workflows/gitleaks.yml so failures are caught before push.
set -uo pipefail
status=0

# The build plan is not published (CLAUDE.md). The filename rules below block it
# by NAME — `guide prompts.pdf`, `Repcut_Prompt_Guide*`. That is not enough: a
# source file that TRANSCRIBES the plan is called something else entirely and
# sails through. This happened — a `prompts_data.py` under engine/ carrying all
# 15 prompt entries passed every rule here, gitleaks, and verify-01.
#
# Signature used: three or more DISTINCT wave titles in one file. That is bulk
# transcription. One or two is a quotation — an amendment citing a wave — and
# stays allowed, which is why this counts distinct titles rather than matching
# the phrase "Wave". Measured before shipping: 0 of the tracked files match,
# the transcription that slipped through matched 6.
BUILD_PLAN_WAVE_RE='Wave [0-5][[:space:]]*(—|–|-)[[:space:]]*(Foundation|Magic Core|Differentiators|Moats|Hardening|Public)'
BUILD_PLAN_WAVE_MAX=2

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

  # Content check, deliberately independent of the extension. Skipped for a path
  # that is not a readable regular file: pre-commit passes deletions, and
  # verify_00 probes this guard with a filename that does not exist.
  [ -f "$f" ] || continue
  waves=$(grep -ohE "$BUILD_PLAN_WAVE_RE" "$f" 2>/dev/null | sort -u | grep -c .)
  if [ "${waves:-0}" -gt "$BUILD_PLAN_WAVE_MAX" ]; then
    echo "BLOCKED (build plan transcribed into the repo, not published): $f"
    echo "        $waves distinct wave titles found. The plan lives outside this"
    echo "        repo — see CLAUDE.md. Reference prompts by number, not by copying"
    echo "        their content. Quoting one or two in docs/guide-amendments/ is fine."
    status=1
  fi
done

if [ -f .env.example ] && grep -Eq '^[A-Z_]+=[[:space:]]*[A-Za-z0-9_-]{16,}' .env.example; then
  echo "BLOCKED: .env.example appears to contain a real value. Key names with EMPTY values only."
  status=1
fi

[ "$status" -ne 0 ] && echo "--- Nothing above may be committed. This repository is public. ---"
exit "$status"
