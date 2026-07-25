# Rule: Secrets & credentials — ABSOLUTE

**The Repcut repository is PUBLIC.** Every commit is world-readable
permanently. Automated scrapers harvest keys from public GitHub within minutes
of a push. Deleting a file does not remove it from history.

## Never, under any circumstances

Do not commit, push, log, print, or write into any tracked file:

- API keys: `GEMINI_API_KEY`, or any string matching `AIza…`, `sk-…`, `hf_…`,
  `ghp_…`, `gho_…`, `github_pat_…`, `xoxb-…`, `AKIA…`
- GitHub tokens, PATs, deploy keys, SSH private keys
- OAuth client secrets, refresh tokens, bearer tokens, session cookies
- Certificates and key material: `.pem`, `.key`, `.p12`, `.pfx`, `.jks`
- Database URLs or any URL with embedded credentials (`user:pass@host`)
- Private endpoints, ngrok/tunnel URLs, personal server links, webhook URLs
- Real `.env`, `.env.local`, `.env.production` files
- Personal data: email addresses, real names in config, absolute paths
  containing the OS username (e.g. `C:\Users\<name>\…`), device IDs
- Media: gym footage, any user video, photos, music files

## Required practice

1. Secrets live in `.env` only. `.env` is gitignored and stays that way.
2. `.env.example` holds **key names with empty values**. Never a real value,
   never a plausible-looking dummy that could be mistaken for real.
3. Config is read via `pydantic-settings` (engine) or `process.env` (UI).
   Zero hardcoded credentials, zero hardcoded absolute paths.
4. Never interpolate a secret into: log lines, exception messages, test
   fixtures, snapshot files, `docs/reports/prompt-NN.md`, code comments, or
   commit messages.
5. Redact in logs: log `GEMINI_API_KEY set: true`, never the value or a prefix.
6. Before any commit, `gitleaks` runs via pre-commit. In CI it runs again on
   every push. **Never** use `git commit --no-verify` or add a gitleaks
   allowlist entry to make a scan pass.
7. Paths in committed code/docs must be repo-relative or env-derived. If you
   need to reference the machine, use `$DATA_DIR`, never a literal user path.

## If a secret is committed

Stop everything. Do not try to fix it quietly.

1. Tell the human immediately and explicitly.
2. State that the key must be treated as **permanently compromised** —
   the moment it hit a public repo it was public, and history rewriting does
   not undo that.
3. Direct them to revoke and rotate at the provider (Google AI Studio for
   `GEMINI_API_KEY`) before anything else.
4. Only after rotation, clean the working tree and add the pattern to
   `.gitignore` so it cannot recur.

## Also never

- `git push --force` to `main`
- Committing to `main` directly (branch-protected; use `/gate NN`)
- Disabling, skipping, or weakening the gitleaks CI job
