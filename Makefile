.PHONY: help dev test test-gpu lint format secrets clean verify-00 verify-01 verify-02 verify-03 \
        verify-04 verify-05 verify-06 verify-07 verify-08 verify-09 verify-10 \
        verify-11 verify-12 verify-13

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev:  ## Run engine (:8000) + UI (:3000) concurrently
	@echo "Implemented in Prompt 01"

test:  ## Full CPU test suite (GPU tests excluded)
	@echo "Implemented in Prompt 01"

test-gpu:  ## GPU-marked tests. Local machine only, never CI.
	@echo "Implemented in Prompt 01"

lint:  ## ruff + mypy + eslint + tsc
	@echo "Implemented in Prompt 01"

format:  ## ruff format + prettier
	@echo "Implemented in Prompt 01"

secrets:  ## Scan working tree AND full history for leaked credentials
	@command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks not installed: https://github.com/gitleaks/gitleaks"; exit 1; }
	gitleaks detect --source . --redact --verbose
	gitleaks protect --staged --redact --verbose || true

clean:  ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache ui/.next

verify-00:  ## Gate for Prompt 00 — the agent harness itself
	@bash scripts/verify_00.sh

# Each verify-NN is authored by the prompt it gates. Binary, exit 1 on failure.
verify-01 verify-02 verify-03 verify-04 verify-05 verify-06 verify-07 \
verify-08 verify-09 verify-10 verify-11 verify-12 verify-13:
	@echo "Gate $@ not implemented yet — authored by the prompt it gates."; exit 1
