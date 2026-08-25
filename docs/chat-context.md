# Repcut — chat handoff context

One Claude chat per prompt. Paste the **Current block** at the top of a new chat
to start the next prompt's thinking session. Update this file once per gate —
it takes two minutes and saves re-deriving state every time.

This is context for the *thinking layer* (planning, review, debugging). It is
not the Claude Code prompt itself; that gets written inside the chat.

---

## CURRENT BLOCK — paste this to start the Prompt 02 chat

```
Repcut status handoff. Read this before responding.

POSITION
- Prompt 01 is gated and closed: PR #2 merged to main, tagged `prompt-01-done`,
  docs/reports/prompt-01.md written. Gate was green.
- Wave 0 (Prompts 00-01) is complete. Next up: Prompt 02, first prompt of Wave 1.
- main HEAD: 1f58306. Working tree clean.

WHAT EXISTS IN THE REPO
- .claude/ harness: 12 agents, 10 commands, 9 binding rules, 8 skills (Prompt 00)
- engine/: FastAPI app — config.py (pydantic-settings), logging.py (structlog
  JSON), main.py (GET /health), models.py, probes.py, 3 test files
- ui/: Next.js 14 App Router, TS strict, Tailwind, dark tokens in
  app/globals.css + tailwind.config.ts, /status page parsing /health with Zod,
  lib/health.ts + vitest test
- scripts/: check_env.py, dev.sh, setup.sh, precommit_guard.sh, verify_00.sh,
  verify_01.sh (490 lines, 12 criteria)
- Makefile with real targets: setup, dev, test, test-gpu, lint, format,
  check-env, secrets, clean, verify-00, verify-01
- CI: ci.yml (engine + ui + guardrails), gitleaks.yml, tag-gate.yml — all green
- .env exists, gitignored, GEMINI_API_KEY and REPCUT_GUIDE_PATH both set

AMENDMENTS IN FORCE (docs/guide-amendments/)
- 000 — Prompt 00 agent harness exists; not in the original guide. ACCEPTED
- 001 — CI jobs gated on scaffolding presence; fixed a setup-python cache bug
  on an unscaffolded tree. ACCEPTED
- 002 — Prompt 01 scope reduced: the guide assumed an empty folder, but Prompt
  00 already delivered CLAUDE.md, .gitignore, git and CI. ACCEPTED
- 003 — torch DEFERRED to Prompt 07. Nothing before Prompt 07 imports it (RIFE
  is 07, YOLO is 10, captions use faster-whisper/CTranslate2). The guide's
  "status page shows CUDA true" criterion moves to Prompt 07's gate.
  ACCEPTED

STANDING CONSTRAINTS BEYOND CLAUDE.md
- Do NOT install torch/torchvision/torchaudio. ~2.3GB CUDA wheel, unused until
  Prompt 07, and a previous attempt disconnected mid-download.
- make is GNU Make 3.81 (mingw32, 2006). No `.ONESHELL` — every recipe line
  runs in its own shell, so multi-step recipes must be one line chained with
  `&&`. Run make from Git Bash, not PowerShell.
- Branch `project-process-dashboard` is off-plan personal work, pushed to
  origin, NOT merged to main. Do not touch it in prompt sessions. It has one
  open fix: engine/repcut/prompts_data.py hardcodes the build-plan roadmap into
  a public repo, which contradicts CLAUDE.md. Must be refactored to read from
  the gitignored guide before that branch ever merges.
- The repo is PUBLIC. Secrets rules in .claude/rules/secrets.md are absolute.

BUILDER CONTEXT
Ashwin, ~5 hrs/week, €0 budget, RTX 3050 (4GB VRAM) laptop. Prefer the smallest
correct step over the impressive one. Claude Code executes the build prompts
autonomously; this chat is the thinking layer.

WHAT I WANT FROM THIS CHAT
Prompt 02. Known guide conflicts already
identified, carry them in:
1. Success criteria demand three real phone clips (incl. HEVC/VFR), but
   testing.md forbids committing media. Split into a synthetic-fixture gate
   plus a manual real-footage checklist.
2. ffmpeg_builder path is inconsistent — guide says engine/ffmpeg_builder.py,
   ffmpeg.md says engine/media/ffmpeg_builder.py, package is engine/repcut/.
   Resolve to engine/repcut/media/ffmpeg_builder.py.
3. "2GB upload, RSS < 500MB" cannot run in CI or the fast loop — make it a
   slow-marked, disk-gated test generating the file at test time.
These become amendment 004, written as Prompt 02's first deliverable.

Start by confirming you have the guide's Prompt 02 section, then help me with
[plan review / session report review / debugging / whatever this chat is for].
```

---

## REUSABLE TEMPLATE — for Prompt 03 and beyond

Copy, fill the bracketed parts, paste into a fresh chat.

```
Repcut status handoff. Read this before responding.

POSITION
- Prompt [NN-1] is gated and closed: merged to main, tagged `prompt-[NN-1]-done`,
  docs/reports/prompt-[NN-1].md written.
- Wave [X], Prompt [NN] is next.
- main HEAD: [sha]. Working tree [clean / has: ...].

WHAT EXISTS IN THE REPO
[Append what the last prompt added. Keep it to modules and their purpose, not
file-by-file — the chat can read the repo.]

AMENDMENTS IN FORCE
[List every amendment number, one line each, with its effect. This is the part
most easily forgotten and most expensive to lose.]

STANDING CONSTRAINTS BEYOND CLAUDE.md
[Carry forward: torch deferral until it lands, make 3.81, the dashboard branch,
plus anything new — pinned model versions, VRAM findings, quota limits hit.]

OPEN ISSUES / DEBT
[Anything a gate passed *around* rather than through. Deferred criteria,
skipped manual checklists, known-flaky tests. If this section is empty, say
"none" explicitly rather than omitting it.]

BUILDER CONTEXT
Ashwin, ~5 hrs/week, €0 budget, RTX 3050 (4GB VRAM) laptop. Prefer the smallest
correct step. Claude Code executes; this chat is the thinking layer.

WHAT I WANT FROM THIS CHAT
Prompt [NN] — [title]. [Known conflicts with the rules, if any.]
Start by confirming you have the guide's Prompt [NN] section, then
[write the Claude Code kick-off / review this session report / debug X].
```

---

## Updating this file

After each `/gate NN`, in the same session or the next chat:

1. Move POSITION forward one prompt; update the main HEAD sha.
2. Append what the prompt added to WHAT EXISTS.
3. Add any new amendment to AMENDMENTS IN FORCE.
4. Add anything the gate deferred, skipped or worked around to OPEN ISSUES.
5. Rewrite WHAT I WANT for the next prompt.

The section that matters most is **AMENDMENTS IN FORCE**. Every one of them is
a place where the repo and the build guide disagree, and a fresh chat that
doesn't know about them will confidently recommend the guide's version.

## The five human review gates

Prompts 04, 05, 06, 08, 10 need a taste checkpoint, not just a green gate. Give
those a chat of their own, before running the prompt, to work out what you're
looking for — grades, cut feel, reference match. A gate can prove the code
runs; it cannot tell you the edit looks good.
