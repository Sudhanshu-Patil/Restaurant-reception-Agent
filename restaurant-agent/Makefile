.DEFAULT_GOAL := check
PY ?= python

.PHONY: install
install:  ## Install the package with dev extras
	$(PY) -m pip install -e ".[dev]"

.PHONY: fmt
fmt:  ## Auto-format and fix lint
	ruff format .
	ruff check --fix .

.PHONY: lint
lint:  ## Lint (no changes)
	ruff format --check .
	ruff check .

.PHONY: type
type:  ## Static type check
	mypy agent

.PHONY: test
test:  ## Run the test suite with coverage
	pytest --cov=agent --cov-report=term-missing

.PHONY: check
check: lint type test  ## Everything CI runs

.PHONY: run
run:  ## Serve the REST API + web UI on :8100
	uvicorn agent.api:app --host 0.0.0.0 --port 8100

.PHONY: cli
cli:  ## Start the interactive CLI (set PHONE=...)
	$(PY) -m agent.cli --phone "$(PHONE)" --verbose

.PHONY: openapi
openapi:  ## Regenerate docs/openapi.json
	$(PY) -m agent.tools_dev.export_openapi

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
