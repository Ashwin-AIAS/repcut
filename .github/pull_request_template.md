## Prompt

Closes prompt-**NN**. Wave: **N**.

## What this builds

<!-- One paragraph. What exists now that didn't before. -->

## Gate

Paste the output of `make verify-NN` — every criterion, with measured values:

```
verify-NN
  [PASS] ...
```

- [ ] `make verify-NN` green, every criterion PASS
- [ ] `make lint` clean (ruff, mypy, eslint, tsc)
- [ ] `docs/reports/prompt-NN.md` written
- [ ] **If GPU code changed:** `make test-gpu` run locally and green
      *(CI has no GPU — this is the only place those paths are tested)*
- [ ] **If a taste gate (04, 05, 06, 08, 10):** review artifacts produced and
      human verdict recorded in the report

## Principles

- [ ] **P1** — nothing generated or replaced; RIFE ≤2x with a working
      confidence fallback
- [ ] **P2** — every new AI value is overridable, and overrides re-sync dependents
- [ ] **P3** — overrides logged as taste signals
- [ ] **P4** — nothing beyond one sampled frame per scene leaves the machine;
      disclosed in the UI
- [ ] **P5** — no paid service, no card, nothing outside the approved stack

## Secrets — public repository

- [ ] No API key, token, credential, private URL, or `.env` file in this diff
- [ ] No media, model weights, or `data/` content committed
- [ ] No absolute path containing a username
- [ ] `gitleaks` CI green

## Deviations from the build plan

<!-- None, or link docs/guide-amendments/NNN-*.md -->
