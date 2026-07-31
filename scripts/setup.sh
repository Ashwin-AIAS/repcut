#!/usr/bin/env bash
# Idempotent developer setup: virtualenv, engine dependencies, UI dependencies.
# Safe to re-run after a crash. Installs nothing that costs money (P5) and
# downloads no model weights — those arrive with the prompts that need them.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

status=0
step() { echo; echo "── $* ──"; }

step "virtualenv"
if [ -x .venv/Scripts/python.exe ] || [ -x .venv/bin/python ]; then
  echo "already present at .venv/"
else
  PYBOOT=""
  for c in python3.11 python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)" >/dev/null 2>&1; then
      PYBOOT="$c"; break
    fi
  done
  if [ -z "$PYBOOT" ]; then
    echo "no Python >= 3.11 found. fix: install Python 3.11 from python.org" >&2
    exit 1
  fi
  "$PYBOOT" -m venv .venv || exit 1
  echo "created .venv/"
fi

PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv/bin/python"

step "engine dependencies"
"$PY" -m pip install --quiet --upgrade pip || status=1
"$PY" -m pip install --quiet -e "engine[dev]" || status=1
"$PY" -c "import repcut; print('repcut', repcut.__version__)" || status=1

step "ui dependencies"
if [ -f ui/package-lock.json ]; then
  ( cd ui && npm ci ) || status=1
elif [ -f ui/package.json ]; then
  ( cd ui && npm install ) || status=1
else
  echo "ui/ not scaffolded yet — skipping"
fi

step "pre-commit hook"
if command -v pre-commit >/dev/null 2>&1; then
  pre-commit install >/dev/null 2>&1 && echo "installed" || echo "already installed"
else
  echo "pre-commit not found. fix: pip install pre-commit && pre-commit install" >&2
  status=1
fi

echo
if [ "$status" -eq 0 ]; then
  echo "setup complete. next: make dev   (or: python scripts/check_env.py)"
else
  echo "setup finished with errors — see above" >&2
fi
exit "$status"
