#!/usr/bin/env bash
# Local guardrail: blocks anything that must never reach a PUBLIC repository.
# Mirrors .github/workflows/gitleaks.yml so failures are caught before push.
set -uo pipefail
status=0

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
done

if [ -f .env.example ] && grep -Eq '^[A-Z_]+=[[:space:]]*[A-Za-z0-9_-]{16,}' .env.example; then
  echo "BLOCKED: .env.example appears to contain a real value. Key names with EMPTY values only."
  status=1
fi

[ "$status" -ne 0 ] && echo "--- Nothing above may be committed. This repository is public. ---"
exit "$status"
