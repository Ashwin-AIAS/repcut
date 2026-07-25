# Rule: Git & CI

## Branching
- One prompt = one branch: `prompt-NN`. Created by `/run-prompt NN`.
- Push to the prompt branch freely and often — it is free offsite backup.
- **Never push directly to `main`.** `main` is branch-protected: PR + green CI.
- Merge to `main` happens ONLY via `/gate NN`, which requires:
  1. `make verify-NN` green, every success criterion PASS
  2. CI green on the PR
  3. `docs/reports/prompt-NN.md` written
  4. For prompts touching GPU code: `make test-gpu` run locally and green
- After merge: tag `prompt-NN-done`.

## Commits
- Format: `prompt-NN: <imperative summary>`. Body explains *why* when non-obvious.
- Commit granularly during a prompt; don't batch a whole session into one blob.
- **Never** `--no-verify`. **Never** `push --force` to `main`.
- Never commit: secrets (see `secrets.md`), `data/`, media files, models,
  `node_modules`, `.env`, the build-plan documents.

## CI (GitHub Actions, public repo — unlimited free minutes)
Three workflows:
- `ci.yml` — on PR to `main`: ruff, mypy, pytest `-m "not gpu"`, tsc, eslint,
  vitest, `next build`. ffmpeg installed via apt.
- `gitleaks.yml` — on every push and PR. Non-negotiable, never skipped.
- `tag-gate.yml` — on tag `prompt-NN-done`: asserts `docs/reports/prompt-NN.md`
  exists and is non-trivial.

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
