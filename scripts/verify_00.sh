#!/usr/bin/env bash
# Gate for Prompt 00 — Agent Harness.
# Binary, exit-coded, per-criterion, idempotent. Same contract as every other
# verify-NN. See .claude/skills/verify-gate-authoring/SKILL.md
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# Resolve a working python. `python3` can be a broken pyenv shim on Windows and
# is absent on some systems; try candidates until one actually executes.
PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done

pass=0; fail=0
ok()   { printf "  [PASS] %-52s %s\n" "$1" "${2:-}"; pass=$((pass+1)); }
no()   { printf "  [FAIL] %-52s %s\n" "$1" "${2:-}"; fail=$((fail+1)); }
chk()  { if [ "$1" = 0 ]; then ok "$2" "$3"; else no "$2" "$3"; fi; }

echo "verify-00 — agent harness"

# 1. Component counts
n_agents=$(ls .claude/agents/*.md 2>/dev/null | wc -l)
n_cmds=$(ls .claude/commands/*.md 2>/dev/null | wc -l)
n_rules=$(ls .claude/rules/*.md 2>/dev/null | wc -l)
n_skills=$(ls -d .claude/skills/*/SKILL.md 2>/dev/null | wc -l)
[ "$n_agents" -ge 12 ]; chk $? "12 agents present" "($n_agents)"
[ "$n_cmds"   -ge 10 ]; chk $? "10 commands present" "($n_cmds)"
[ "$n_rules"  -ge 9  ]; chk $? "9 rules present" "($n_rules)"
[ "$n_skills" -ge 8  ]; chk $? "8 skills present" "($n_skills)"

# 2. Frontmatter valid, names match filenames
bad=""
for f in .claude/agents/*.md; do
  head -1 "$f" | grep -q '^---$' || bad="$bad $f(no-fm)"
  nm=$(sed -n 's/^name:[[:space:]]*//p' "$f" | head -1)
  [ "$nm" = "$(basename "$f" .md)" ] || bad="$bad $f(name)"
  grep -q '^description:' "$f" || bad="$bad $f(desc)"
done
for f in .claude/commands/*.md; do
  head -1 "$f" | grep -q '^---$' || bad="$bad $f(no-fm)"
  grep -q '^description:' "$f" || bad="$bad $f(desc)"
done
for f in .claude/skills/*/SKILL.md; do
  nm=$(sed -n 's/^name:[[:space:]]*//p' "$f" | head -1)
  [ "$nm" = "$(basename "$(dirname "$f")")" ] || bad="$bad $f(name)"
done
[ -z "$bad" ]; chk $? "frontmatter valid, names match paths" "${bad:-}"

# 3. Every @-imported rule in CLAUDE.md resolves
miss=""
while read -r r; do [ -f "$r" ] || miss="$miss $r"; done < <(grep -oE '^@\.claude/rules/\S+' CLAUDE.md | tr -d '@')
[ -z "$miss" ]; chk $? "CLAUDE.md rule imports resolve" "${miss:-}"

# 4. P1-P5 invariants and the secrets policy are in CLAUDE.md
missp=""
for p in "P1 Natural only" "P2 AI recommends" "P3 " "P4 " "P5 "; do
  grep -q "$p" CLAUDE.md || missp="$missp[$p]"
done
grep -qi "never commit" CLAUDE.md || missp="$missp[secrets-policy]"
[ -z "$missp" ]; chk $? "CLAUDE.md carries P1-P5 + secrets policy" "${missp:-}"

# 5. .env.example holds key names with EMPTY secret values
if [ -f .env.example ]; then
  grep -Eq '^[A-Z_]+=[[:space:]]*[A-Za-z0-9_-]{16,}' .env.example && r=1 || r=0
  grep -q '^GEMINI_API_KEY=$' .env.example || r=1
else r=1; fi
chk $r ".env.example: key names, empty secret values" ""

# 6. No credential-shaped string anywhere in tracked-eligible files
hits=$(grep -rEl "AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}" \
       --include="*.md" --include="*.yml" --include="*.yaml" --include="*.json" --include="*.sh" --include="*.py" . 2>/dev/null || true)
[ -z "$hits" ]; chk $? "no credential-shaped strings in repo" "${hits:-}"

# 7. .gitignore actually blocks what it must (tested, not assumed)
tmp=$(mktemp -d); cp .gitignore "$tmp/"; ( cd "$tmp" && git init -q . )
mkdir -p "$tmp/data" "$tmp/docs/reviews/p04"
touch "$tmp/data/c.mp4" "$tmp/s.mp3" "$tmp/m.pt" "$tmp/.env" "$tmp/k.pem" \
      "$tmp/Repcut_Prompt_Guide_v9.md" "$tmp/notes.docx" "$tmp/docs/reviews/p04/a.png" \
      "$tmp/.env.example" "$tmp/CLAUDE.md" "$tmp/README.md"
leak=""
for f in data/c.mp4 s.mp3 m.pt .env k.pem Repcut_Prompt_Guide_v9.md notes.docx docs/reviews/p04/a.png; do
  ( cd "$tmp" && git check-ignore -q "$f" ) || leak="$leak $f"
done
wrong=""
for f in .env.example CLAUDE.md README.md; do
  ( cd "$tmp" && git check-ignore -q "$f" ) && wrong="$wrong $f"
done
rm -rf "$tmp"
[ -z "$leak" ] && [ -z "$wrong" ]; chk $? "gitignore blocks media/env/plan, keeps source" "${leak:-}${wrong:-}"

# 8. Pre-commit guard executable and actually blocks
if [ -x scripts/precommit_guard.sh ]; then
  bash scripts/precommit_guard.sh a.mp4 >/dev/null 2>&1 && r=1 || r=0
  bash scripts/precommit_guard.sh engine/x.py >/dev/null 2>&1 && r2=0 || r2=1
  [ "$r" = 0 ] && [ "$r2" = 0 ]; chk $? "pre-commit guard blocks bad, allows good" ""
else no "pre-commit guard blocks bad, allows good" "(script missing or not executable)"; fi

# 9. Workflows parse and gitleaks job is intact
wf=0
if [ -z "$PY" ]; then wf=1; fi
for f in .github/workflows/ci.yml .github/workflows/gitleaks.yml .github/workflows/tag-gate.yml; do
  [ -f "$f" ] || wf=1
  "$PY" -c "import yaml,sys;yaml.safe_load(open(sys.argv[1],encoding='utf-8'))" "$f" 2>/dev/null || wf=1
done
grep -q "gitleaks" .github/workflows/gitleaks.yml || wf=1
"$PY" -c "import yaml;yaml.safe_load(open('.pre-commit-config.yaml',encoding='utf-8'))" 2>/dev/null || wf=1
"$PY" -c "import json;json.load(open('.claude/settings.json',encoding='utf-8'))" 2>/dev/null || wf=1
chk $wf "workflows + pre-commit + settings parse" ""

# 10. Shell scripts are LF (CRLF silently breaks the guard on Windows)
# Counted as raw bytes, not via grep: MSYS/Git-Bash grep handles -U and \r
# differently from GNU grep on Linux and reports false positives. A byte count
# is unambiguous on every platform.
if [ -n "$PY" ]; then
  crlf=$("$PY" - <<'PYEOF' 2>/dev/null
import glob, sys
bad = []
for f in sorted(glob.glob("scripts/*.sh")) + ["Makefile", ".claude/hooks/format_edited.sh"]:
    try:
        n = open(f, "rb").read().count(b"\r")
    except OSError:
        continue
    if n:
        bad.append(f"{f}({n} CR)")
print(" ".join(bad))
PYEOF
)
  [ -z "$crlf" ]; chk $? "no CRLF in shell scripts / Makefile" "${crlf:-}"
else
  no "no CRLF in shell scripts / Makefile" "(no working python found)"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "PASSED: $pass of $((pass+fail)) criteria"; exit 0
else
  echo "FAILED: $fail of $((pass+fail)) criteria"; exit 1
fi
