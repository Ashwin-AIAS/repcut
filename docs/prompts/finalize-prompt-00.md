# Finalize Prompt 00 — close the gate, then start Prompt 01

Paste the PROMPT section into Claude Code, in the repo folder.
Every step is idempotent: it checks current state and skips work already done.

## Two things Claude Code cannot do

1. **Write `.env`.** `.claude/settings.json` denies `Read`/`Write`/`Edit` on it
   by design — that guardrail stays. Claude Code will tell you what to put in
   it; you edit it.
2. **Change GitHub branch-protection settings** if the API call fails — it will
   print the web-UI steps instead of guessing.

---

## PROMPT — Finalize Prompt 00

### Role & Context

```
You are closing out Prompt 00 (agent harness) for Repcut so Prompt 01 can
start from a clean, gated `main`.

Current known state: repo published to github.com/Ashwin-AIAS/repcut (public,
AGPL-3.0). `main` has commits 834de4a (initial) and 05bfa4a (harness). Branch
`fix/ci-unscaffolded` holds two unmerged commits: a ci.yml fix and amendment
001. No tags exist. verify-00 passes 13/13.

Verify each item below rather than trusting this description — some may already
be done. Work autonomously per the autonomy protocol. Report a final table of
what you found already-done vs what you changed.
```

### Deliverables

1. **Re-activate the local secret guard.** Check whether `.git/hooks/pre-commit`
   exists and references pre-commit. If not: `pip install pre-commit` then
   `pre-commit install`. Then run `pre-commit run --all-files`.
   The whitespace/EOF hooks may rewrite files on first run — if they do, commit
   the result on the current branch as
   `chore: apply pre-commit formatting` before continuing. Report exactly which
   files changed.

2. **`.env`.** If absent, copy `.env.example` to `.env`. Then STOP on this item
   and tell Ashwin to fill in two keys himself:
   - `REPCUT_GUIDE_PATH=./Repcut_Prompt_Guide_v1.md` (relative — keeps his
     username out of any config file)
   - `GEMINI_API_KEY=` (from Google AI Studio)

   Do not read, write, print, or guess either value. Confirm only that `.env`
   exists and is gitignored (`git check-ignore .env`). Report each key as
   `set: true/false` by checking for a non-empty value **without printing it**.

3. **Merge the CI fix.** If `fix/ci-unscaffolded` has commits not on
   `origin/main`:
   - `gh pr create --base main --head fix/ci-unscaffolded --fill`
   - `gh pr checks --watch` — all checks must pass. The `engine` and `ui` jobs
     should now be **skipped or green**, not red; that is the whole point of the
     fix. `Secret scan` and `Repo guardrails` must be green.
   - `gh pr merge --squash --delete-branch`
   If it is already merged, say so and move on.

4. **Gate `main`.** `git checkout main && git pull`, then
   `bash scripts/verify_00.sh`. Must be **13/13**. If not, STOP and report —
   do not tag a failing gate.

5. **Tag.** If `prompt-00-done` does not exist:
   `git tag prompt-00-done && git push --tags`. Then `gh run list --limit 5`
   and confirm the `tag-gate` workflow passed (it asserts
   `docs/reports/prompt-00.md` exists with its required sections).

6. **Branch protection.** Check
   `gh api repos/Ashwin-AIAS/repcut/branches/main/protection`. `main` must
   require a pull request and require the `Secret scan` and `Repo guardrails`
   checks. If protection is absent or incomplete, try to set it via `gh api`;
   if that errors, print the exact web-UI steps (Settings → Branches → Add
   branch ruleset) rather than guessing at the API shape.

7. **`make`.** Run `make --version`. If absent, report it as a **blocker for
   Prompt 01** — every prompt's gate is a make target (`make verify-01`,
   `make dev`, `make test-gpu`). Suggest `winget install GnuWin32.Make` or WSL.
   Do not rewrite the Makefile into scripts to work around it.

8. **Amendments.** Report the `Status:` line of every file in
   `docs/guide-amendments/`. Both are currently `PROPOSED`. Flipping them to
   `ACCEPTED` is **Ashwin's decision** — ask, do not decide. If he accepts,
   update the status lines and commit as
   `docs: accept amendments 000 and 001`.

### Constraints

- **Never** `git commit --no-verify`, **never** `git push --force`, **never**
  push directly to `main` — merge only through the PR.
- **Never** weaken, edit, or skip `.github/workflows/gitleaks.yml`.
- Never read or print any value from `.env`. Report `set: true/false` only.
- If a credential-shaped string appears anywhere, STOP: do not commit, do not
  push, tell Ashwin, and state that the key must be rotated at the provider.
- Do not start Prompt 01 in this session. Prompt 01 gets a fresh session with
  clean context — that is the session protocol.

### Autonomy Protocol

Autonomous for all steps except: filling `.env` (step 2, Ashwin), accepting
amendments (step 8, Ashwin's decision), and anything touching a secret.

### Success Criteria

- `.git/hooks/pre-commit` active; `pre-commit run --all-files` clean
- `.env` exists, is gitignored, and both required keys report `set: true`
- `origin/main` contains the ci.yml fix; `fix/ci-unscaffolded` deleted
- `bash scripts/verify_00.sh` on `main` → **13/13**
- Tag `prompt-00-done` pushed; `tag-gate` workflow green
- `ci` no longer red on an unscaffolded tree
- `main` branch-protected: PR required + `Secret scan` and `Repo guardrails` required
- `make --version` succeeds, or its absence is reported as a blocker
- Amendment statuses reported, and updated if Ashwin accepted
- `git ls-files` contains no env file, media, model weight, build-plan doc,
  `.pdf` or `.docx`

### Then

Open a **fresh session** and run `/run-prompt 01`.
