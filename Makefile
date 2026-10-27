# Developer entrypoints (AGENTS.md). `make help` lists the targets.

UV ?= uv
# plan.md §3: two pytest workers on every machine, never -n auto.
PYTEST_WORKERS ?= 2

.DEFAULT_GOAL := help
.PHONY: help sync lint fmt types test test-db drift check migrate evals gen-client api worker

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-11s %s\n", $$1, $$2}'

sync: ## Install every workspace member exactly as locked
	$(UV) sync --locked

lint: ## ruff lint and format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Apply ruff fixes and formatting
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

types: ## mypy (strict on vitrina_core)
	$(UV) run mypy

test: ## pytest without live providers (NFR-07)
	$(UV) run pytest -n $(PYTEST_WORKERS)

test-db: ## Migration and database tests; needs DATABASE_URL (CI runs them on its Postgres)
	$(UV) run pytest -m db -p no:xdist

drift: ## Generated OpenAPI vs contracts/openapi.yaml (T003)
	$(UV) run python scripts/check-contract-drift.py

check: lint types test drift ## Everything a commit must pass

migrate: ## Upgrade DB_SCHEMA on DATABASE_URL to the latest migration
	$(UV) run alembic upgrade head

evals: ## Replay evals, MX0 only (runner lands with T028)
	@echo "make evals: no suites yet; the replay runner lands with T028"

gen-client: ## TypeScript client from contracts/openapi.yaml (T013, MX1)
	./scripts/gen-client

api: ## Run the API with reload on :8000
	$(UV) run uvicorn vitrina_api.main:create_app --factory --reload --port 8000

worker: ## Run the job worker
	$(UV) run vitrina-worker
