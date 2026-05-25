.DEFAULT_GOAL := help
.PHONY: help install install-dev test test-live test-all typecheck check build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install swoop with extras (validation + cli)
	python -m pip install -e ".[validation,cli]"

install-dev: ## Install swoop with extras + test/typecheck/build deps (one pip resolve)
	python -m pip install -e ".[validation,cli]" pytest hypothesis pytest-benchmark pyright build

test: ## Run offline test suite (skip live Google Flights tests)
	python -m pytest tests/ -v -m "not live"

test-live: ## Run live integration tests (hits real Google Flights)
	python -m pytest tests/ -v -m live

test-all: ## Run full test suite (offline + live)
	python -m pytest tests/ -v

typecheck: ## Run pyright type checker
	pyright

check: ## Run typecheck + offline tests (pre-PR gate, sequential under -j)
	@$(MAKE) typecheck
	@$(MAKE) test

build: ## Build wheel and sdist
	python -m build

clean: ## Remove build artifacts and caches
	rm -rf dist/ build/ .pytest_cache/ *.egg-info/
	find swoop tests examples scripts -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
