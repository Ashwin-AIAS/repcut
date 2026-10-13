# Rule: Git & CI

## Branching (amendments 015, 016)
- One prompt = one branch, named `feat/<slug>` (or `docs/<slug>` for a
  documentation-only prompt): 2–4 lowercase words **in our own words, never a
  guide title** (amendment 006). Run `scripts/check_plan_titles.py` on the name
  before first use. Prompt 04 keeps `prompt-04`; earlier `prompt-NN` branches
  and tags stay as they are.
- Branches stack: each prompt's branch starts from the previous prompt's branch
  tip, not from `main`, until the deferred human pass (amendment 016).
- The branch is named in the report header (`Branch: <name>`).
- Push the prompt branch freely and often — it is free offsite backup.
- **Never push directly to `main`.** `main` is branch-protected: PR + green CI.
- A prompt is built when `make verify-NN-auto` is green. Merge to `main` happens
  ONLY via `/gate NN`, after the deferred pass
  (`docs/manual-checks/VERIFY-LATER.md`), which requires:
  1. `REPCUT_GATE_STRICT=1 make verify-NN` green, every criterion PASS,
     `[HUMAN]` included
  2. CI green on the PR
  3. `docs/reports/prompt-NN.md` written
  4. For prompts touching GPU code: `make test-gpu` run locally and green
- After merge: tag `v0.N.0` (Prompt 05 → `v0.5.0`). Prompt 04 is tagged
  `prompt-04-done`. Never tag before the stage is verified.

## Commits
- Conventional Commits: `<type>(<scope>): <imperative summary>`; type one of
  `feat fix docs test refactor perf ci chore`; scope optional and short
  (`engine`, `ui`, `gemini`, `colour`, `gate`). No prompt number in the subject
  (amendment 015). Body explains *why* when non-obvious.
- Commit granularly during a prompt; don't batch a whole session into one blob.
- **Never** `--no-verify`. **Never** force-push, rewrite history or delete a
  branch. If the pre-commit tool is missing in a shell, run
  `scripts/precommit_guard.sh` on the staged files plus
  `scripts/check_plan_titles.py` and `scripts/check_plan_leak.py` by hand, and
  say so in the report.
- Never commit: secrets (see `secrets.md`), `data/`, media files, models,
  `node_modules`, `.env`, the build-plan documents, `worklog/`.
- **Nor the build plan's contents in any other form.** Prompt titles,
  summaries, deliverables, wave structure and calendar estimates are the plan
  whether they arrive as a `.pdf`, a Python list, a fixture or a comment. Read
  them at runtime from `REPCUT_GUIDE_PATH`; never retype them into a tracked
  file. `verify-01` criterion 13 matches content, not filenames (amendment 006).

## CI (GitHub Actions, public repo — unlimited free minutes)
Three workflows:
- `ci.yml` — on PR to `main`: ruff, mypy, pytest `-m "not gpu"`, tsc, eslint,
  vitest, `next build`. ffmpeg installed via apt.
- `gitleaks.yml` — on every push and PR. Non-negotiable, never skipped.
- `tag-gate.yml` — on tag `prompt-NN-done`: asserts `docs/reports/prompt-NN.md`
  exists and is non-trivial. The `v0.N.0` trigger (amendment 015) is still
  owed, together with the PR template, before the first `v0.N.0` tag.

## CI constraints that shape design
- **No GPU on runners.** All CUDA/RIFE/YOLO/Whisper tests are `@pytest.mark.gpu`
  and skipped. Consequence: those paths are only tested locally — run
  `make test-gpu` before gating.
- **No real footage in the repo.** Test fixtures are generated at test time with
  `ffmpeg -f lavfi` (synthetic color/motion clips) via a `conftest.py` factory.
  Never commit a `.mp4`, even a tiny one.
- **No secrets in CI.** If a workflow ever needs one, it uses GitHub Actions
  Secrets — never a literal, never `echo`'d into logs.
- Pin action versions to a tag. Cache pip and npm to keep runs fast.

## Recovery
If CI fails, fix the code — never weaken the gate, never add an ignore, never
mark a failing test skip to get green.
