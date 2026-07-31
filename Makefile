.PHONY: help setup setup-gpu dev test test-gpu lint format secrets clean check-env \
        verify-00 verify-01 verify-02 verify-03 \
        verify-04 verify-05 verify-06 verify-07 verify-08 verify-09 verify-10 \
        verify-11 verify-12 verify-13

# Prefer the project virtualenv when it exists, so no target depends on what
# happens to be on PATH. Pure-make detection — no shell call, because `make`
# on Windows may hand $(shell ...) to cmd.exe.
ifneq ($(wildcard .venv/Scripts/python.exe),)
PY := .venv/Scripts/python.exe
else ifneq ($(wildcard .venv/bin/python),)
PY := .venv/bin/python
else
PY := python
endif

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the venv, install engine[dev] and UI deps. Idempotent.
	@bash scripts/setup.sh

setup-gpu:  ## Install a CUDA build of torch into the venv (~2.6GB, free)
	$(PY) -m pip install torch --index-url https://download.pytorch.org/whl/cu124

check-env:  ## Diagnose the dev environment, with a named fix per failure
	@$(PY) scripts/check_env.py

dev:  ## Run engine (:8000) + UI (:3000) concurrently
	@bash scripts/dev.sh

test:  ## Full CPU test suite (GPU tests excluded)
	$(PY) -m pytest engine -m "not gpu" -q
	cd ui && npm run test

test-gpu:  ## GPU-marked tests. Local machine only, never CI.
	$(PY) -m pytest engine -m gpu -q

lint:  ## ruff + mypy + eslint + tsc
	$(PY) -m ruff check engine
	$(PY) -m ruff format --check engine
	$(PY) -m mypy --config-file engine/pyproject.toml engine
	cd ui && npm run lint && npx tsc --noEmit

format:  ## ruff format + prettier
	$(PY) -m ruff format engine
	cd ui && npx prettier --write .

secrets:  ## Scan working tree AND full history for leaked credentials
	@command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks not installed: https://github.com/gitleaks/gitleaks"; exit 1; }
	gitleaks detect --source . --redact --verbose
	gitleaks protect --staged --redact --verbose || true

clean:  ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache ui/.next

verify-00:  ## Gate for Prompt 00 — the agent harness itself
	@bash scripts/verify_00.sh

verify-01:  ## Gate for Prompt 01 — engine & UI scaffold, dev environment
	@bash scripts/verify_01.sh

# Each verify-NN is authored by the prompt it gates. Binary, exit 1 on failure.
verify-02 verify-03 verify-04 verify-05 verify-06 verify-07 \
verify-08 verify-09 verify-10 verify-11 verify-12 verify-13:
	@echo "Gate $@ not implemented yet — authored by the prompt it gates."; exit 1
