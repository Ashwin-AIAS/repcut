# Bootstrap — Publish Repcut to GitHub

One-time. Paste the block below into Claude Code, in the repo folder.

## Do this yourself first (Claude Code cannot — it needs a browser)

```bash
winget install GitHub.cli
gh auth login          # choose HTTPS, authenticate in browser
gh auth status         # must show Logged in
```

---

## PROMPT — Publish repository

### Role & Context

```
You are publishing the Repcut repository to GitHub for the first time. The
agent harness (Prompt 00) is complete and `make verify-00` passes 13/13. The
repository does not exist on GitHub yet.

This repository will be PUBLIC. Read .claude/rules/secrets.md before doing
anything. `gh` is installed and authenticated — verify, do not run `gh auth
login` yourself.

Work autonomously per the autonomy protocol, with the one stop defined below.
```

### Deliverables

1. Run `make verify-00`. If it is not 13/13, STOP and report — do not publish
   a repo that fails its own gate.
2. Run `bash scripts/precommit_guard.sh $(git ls-files -o --exclude-standard)`
   and confirm it exits 0.
3. `git init` (if needed), `git add -A`, and **before committing** print
   `git status --short` and confirm none of the following are staged:
   any `.env`, any media (`.mp4/.mov/.mp3/...`), model weights, anything under
   `data/` except `data/.gitkeep`, `docs/reviews/*` except `.gitkeep`,
   `Repcut_Prompt_Guide_v1.md`, `Repcut_Project_Instructions.md`, any `.pdf`
   or `.docx`. If any appear, STOP and report — the `.gitignore` is wrong.
4. Commit: `prompt-00: agent harness`
5. Create the remote:
   `gh repo create repcut --public --license agpl-3.0 --source=. --remote=origin --description "AI video editor for gym content. Natural, beat-synced, captioned edits from raw footage. Local-first, runs on one laptop."`
6. `git pull --rebase origin main` to bring down the LICENSE file `gh` created.
   Confirm `LICENSE` exists and contains "GNU AFFERO GENERAL PUBLIC LICENSE".
7. **STOP.** Print the exact push command for Ashwin to run himself:
   `git push -u origin main`
   Do not run it — `.claude/settings.json` denies pushing to `main` by design,
   and that guardrail stays intact.
8. After he confirms the push succeeded:
   - Enable branch protection on `main`: require a pull request, require the
     `ci` and `gitleaks` status checks, do not require approvals (solo repo).
     Use `gh api` or, if that is fiddly, print the exact web-UI steps instead
     of guessing at the API shape.
   - Verify both workflows ran: `gh run list --limit 5`. `gitleaks` must be
     green. `ci` will skip its engine/ui jobs (they do not exist until Prompt
     01) — that is expected, not a failure.
   - Tag the harness: `git tag prompt-00-done && git push --tags`, then confirm
     the `tag-gate` workflow passed.

### Constraints

- **Never** `git commit --no-verify`. **Never** `git push --force`.
- **Never** weaken, skip, or edit `.github/workflows/gitleaks.yml`.
- If a credential-shaped string is found at any point, STOP immediately, do not
  push, and tell Ashwin. If it was already pushed, state that the key must be
  treated as permanently compromised and rotated at the provider.
- Do not create a `LICENSE` file yourself — licence text must be verbatim and
  `gh repo create --license agpl-3.0` supplies the official text.
- Do not run `gh auth login`; it needs an interactive browser.

### Autonomy Protocol

Autonomous for every step except: the push to `main` (step 7, handed to
Ashwin), and any situation involving a secret, which stops everything.

### Success Criteria

- `make verify-00` → 13/13
- `github.com/<user>/repcut` exists, public, with an AGPL-3.0 `LICENSE`
- `git ls-files` contains **no** env file, media, model weight, build-plan
  document, `.pdf`, `.docx`, or `docs/reviews` content beyond `.gitkeep`
- `gitleaks` workflow green on the first push
- `main` branch-protected: PR required, `ci` + `gitleaks` required
- Tag `prompt-00-done` pushed and `tag-gate` green
- `docs/reports/prompt-00.md` present on `main`

### Then

Start Prompt 01 with `/run-prompt 01`.
