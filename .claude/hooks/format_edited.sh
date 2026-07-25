#!/usr/bin/env bash
# Formats the file Claude Code just edited. Reads the hook payload from stdin
# and extracts the path defensively — the payload shape varies by version, so
# every branch degrades to a no-op rather than failing the tool call.
# Always exits 0: a formatter must never block an edit.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
file=""

if command -v python3 >/dev/null 2>&1 && [ -n "$payload" ]; then
  file="$(printf '%s' "$payload" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
for k in ("file_path", "path", "notebook_path"):
    v = ti.get(k) or d.get(k)
    if isinstance(v, str) and v:
        print(v); break
' 2>/dev/null || true)"
fi

[ -z "$file" ] && file="${CLAUDE_FILE_PATHS:-}"
[ -z "$file" ] || [ ! -f "$file" ] && exit 0

case "$file" in
  *.py)
    command -v ruff >/dev/null 2>&1 && {
      ruff format "$file" >/dev/null 2>&1
      ruff check --fix "$file" >/dev/null 2>&1
    } ;;
  *.ts|*.tsx|*.js|*.jsx|*.css|*.json)
    command -v npx >/dev/null 2>&1 && \
      npx --no-install prettier --write "$file" >/dev/null 2>&1 ;;
esac

exit 0
