# ==============================================================================
# Lean Omega — AI Orchestrator
# ==============================================================================

PYTHON  ?= python
PYTEST  ?= pytest
RUFF    ?= ruff
MYPY    ?= mypy

GOAL    ?= config/goals/example_goal.yaml
STAGE   ?= 1

DOCKER_IMAGE  ?= omega-python-runner
DOCKER_FILE   ?= docker/Dockerfile.python-runner

.DEFAULT_GOAL := help

# ------------------------------------------------------------------------------
# Help
# ------------------------------------------------------------------------------
.PHONY: help
help: ## Show this help message
    @printf "\nUsage: make <target> [VARIABLE=value]\n\n"
    @printf "Variables:\n"
    @printf "  GOAL=<path>     Goal YAML file (default: $(GOAL))\n"
    @printf "  STAGE=<n>       Stage number for test-stage (default: $(STAGE))\n\n"
    @printf "Targets:\n"
    @awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  %-20s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
    @printf "\n"

# ------------------------------------------------------------------------------
# Environment Setup
# ------------------------------------------------------------------------------
.PHONY: setup
setup: ## Create .venv and install all dev dependencies (runs scripts/setup_env.sh)
    bash scripts/setup_env.sh

.PHONY: install
install: ## Re-install project in editable mode with dev extras (no venv creation)
    pip install -e ".[dev]"

.PHONY: install-all
install-all: ## Install all optional dependency groups (stage3–stage5 + dev)
    pip install -e ".[dev,stage3,stage4,stage5]"

# ------------------------------------------------------------------------------
# Code Quality
# ------------------------------------------------------------------------------
.PHONY: lint
lint: ## Run ruff linter on src/ and tests/
    $(RUFF) check src/ tests/

.PHONY: lint-fix
lint-fix: ## Run ruff linter with auto-fix on src/ and tests/
    $(RUFF) check --fix src/ tests/

.PHONY: format
format: ## Run ruff formatter on src/ and tests/
    $(RUFF) format src/ tests/

.PHONY: format-check
format-check: ## Check formatting without writing changes
    $(RUFF) format --check src/ tests/

.PHONY: typecheck
typecheck: ## Run mypy type-checker on src/
    $(MYPY) src/

.PHONY: check
check: lint format-check typecheck ## Run full quality pass: lint + format-check + typecheck

# ------------------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------------------
.PHONY: test
test: ## Run full test suite with verbose output
    $(PYTEST) tests/ -v

.PHONY: test-cov
test-cov: ## Run tests with coverage report
    $(PYTEST) tests/ --cov=src --cov-report=term-missing

.PHONY: test-stage
test-stage: ## Run tests for a specific stage  (e.g. make test-stage STAGE=3)
    $(PYTEST) tests/test_stage$(STAGE).py -v

.PHONY: test-fast
test-fast: ## Run tests, stop on first failure
    $(PYTEST) tests/ -x -q

# ------------------------------------------------------------------------------
# Docker
# ------------------------------------------------------------------------------
.PHONY: docker-build
docker-build: ## Build the omega-python-runner sandbox image
    docker build -f $(DOCKER_FILE) -t $(DOCKER_IMAGE) .

.PHONY: docker-clean
docker-clean: ## Remove the omega-python-runner image
    docker rmi $(DOCKER_IMAGE) || true

.PHONY: docker-check
docker-check: ## Check whether the sandbox image exists locally
    @docker image inspect $(DOCKER_IMAGE) > /dev/null 2>&1 \
        && echo "Image '$(DOCKER_IMAGE)' is present." \
        || echo "Image '$(DOCKER_IMAGE)' not found — run: make docker-build"

# ------------------------------------------------------------------------------
# Orchestrator Runs
# ------------------------------------------------------------------------------
.PHONY: run
run: ## Run orchestrator with sandbox enabled (honours GOAL variable)
    $(PYTHON) main.py --goal $(GOAL) --mode local

.PHONY: run-no-sandbox
run-no-sandbox: ## Run orchestrator without Docker sandbox (honours GOAL variable)
    $(PYTHON) main.py --goal $(GOAL) --mode local --no-sandbox

.PHONY: resume
resume: ## Resume orchestrator from last checkpoint (honours GOAL variable)
    $(PYTHON) main.py --goal $(GOAL) --mode local --resume

.PHONY: resume-no-sandbox
resume-no-sandbox: ## Resume from checkpoint, no sandbox (honours GOAL variable)
    $(PYTHON) main.py --goal $(GOAL) --mode local --resume --no-sandbox

.PHONY: run-example
run-example: ## Run the built-in example goal (sandbox on)
    $(PYTHON) main.py --goal config/goals/example_goal.yaml --mode local

.PHONY: run-auth
run-auth: ## Run the implement_auth goal (sandbox on)
    $(PYTHON) main.py --goal config/goals/implement_auth.yaml --mode local

# ------------------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------------------
.PHONY: clean-runs
clean-runs: ## Delete all run artifacts under runs/ (preserves the directory)
    find runs/ -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +

.PHONY: clean-volumes
clean-volumes: ## Delete generated code under volumes/ (preserves the directory)
    find volumes/ -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +

.PHONY: clean-pycache
clean-pycache: ## Remove all __pycache__ directories
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

.PHONY: clean
clean: clean-runs clean-volumes clean-pycache ## Remove all generated artifacts