# Amendment 001 — ci.yml scaffold-tolerance broke at the setup-action cache step
Date: 2026-07-25
Affects: Prompt 00 (`.github/workflows/ci.yml`), Amendment 000 (Prompt 01 Deliverable 8), publish-repo.md step 8
Status: PROPOSED

## What the guide says

Amendment 000 (Prompt 01, Deliverable 8) records the intended design:

> `gitleaks.yml`, `ci.yml` and `tag-gate.yml` already exist from Prompt 00 with
> **scaffold-tolerant skip conditions**.

`docs/publish-repo.md`, step 8, states the expected behaviour on first publish:

> `ci` will skip its engine/ui jobs (they do not exist until Prompt 01) — that
> is expected, not a failure.

## What we found

On the first push to `main` (commit `05bfa4a`, unscaffolded repo), the `ci`
workflow was **red**, not skipped:

| Check | Result |
|---|---|
| Engine (Python) | ❌ failure |
| UI (Next.js) | ❌ failure |
| Repo guardrails | ✅ success |
| Secret scan (gitleaks) | ✅ success |

Both jobs died at their setup step, before reaching the scaffold-tolerant
skip logic:

- `actions/setup-python@v5` with `cache: pip` errors when there is no
  dependency file to hash.
- `actions/setup-node@v4` with `cache-dependency-path: ui/package-lock.json`
  errors with "Some specified paths were not resolved, unable to cache
  dependencies" when that file does not exist.

The `Install` steps already `exit 0` when unscaffolded, and the later
lint/type/test steps are guarded by `hashFiles(...)`. But the **setup actions
run unconditionally**, so their cache resolution failed first and failed the
whole job.

## Why the guide's version doesn't work

The scaffold-tolerance was implemented one layer too late. It guarded the
install/lint/test steps but not the `setup-python` / `setup-node` steps whose
caching is what actually breaks on an unscaffolded tree. The documented intent
("scaffold-tolerant skip conditions") was therefore not met by the shipped
`ci.yml`.

## Proposed change

Gate the setup steps (and their caches) on the presence of the scaffolding they
depend on, in `.github/workflows/ci.yml`:

```yaml
      - uses: actions/setup-python@v5
        if: hashFiles('engine/pyproject.toml', 'engine/requirements-dev.txt') != ''
        with:
          python-version: "3.11"
          cache: pip
```

```yaml
      - uses: actions/setup-node@v4
        if: hashFiles('ui/package-lock.json') != ''
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: ui/package-lock.json
```

When scaffolded, behaviour is unchanged. When not, the setup steps are skipped
and the jobs run as the intended green no-op.

Verified on PR #1: Engine (Python), UI (Next.js), Repo guardrails, and Secret
scan all pass on the unscaffolded repo.

## Consequences

- Fulfils Amendment 000's stated "scaffold-tolerant" design; does not change it.
- No threshold was weakened and no gate was made to pass by lowering a bar — a
  real CRLF/secret/other defect still fails `ci` and the pre-commit guard.
- Prompt 01 (Deliverable 8) is unaffected: its job is still to make the engine
  and ui jobs actually execute and go green once dependencies and lockfiles
  exist. Once those files land, the `if:` guards evaluate true and the setup
  steps run exactly as before.
- No already-passed gate is invalidated.

## Principle check

Does not touch P1, P2, or P3. Touches P5 only in that it keeps CI on the
public repo's unlimited free GitHub Actions minutes — €0 holds. `gitleaks.yml`
is untouched, so the P4/secrets gate is unchanged.
