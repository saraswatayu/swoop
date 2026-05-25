.DEFAULT_GOAL := help
.PHONY: help install install-dev test test-live test-all typecheck check build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install swoop with extras (validation + cli)
	pip install -e ".[validation,cli]"

install-dev: install ## Install + test deps (pytest, hypothesis, pytest-benchmark)
	pip install pytest hypothesis pytest-benchmark

test: ## Run offline test suite (skip live Google Flights tests)
	python -m pytest tests/ -v -m "not live"

test-live: ## Run live integration tests (hits real Google Flights)
	python -m pytest tests/ -v -m live

test-all: ## Run full test suite (offline + live)
	python -m pytest tests/ -v

typecheck: ## Run pyright type checker
	pyright

check: typecheck test ## Run typecheck + offline tests (pre-PR gate)

build: ## Build wheel and sdist
	python -m build

clean: ## Remove build artifacts and caches
	rm -rf dist/ build/ *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
