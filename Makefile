# Developer entrypoints (AGENTS.md). `make help` lists the targets.

UV ?= uv
# plan.md §3: two pytest workers on every machine, never -n auto.
PYTEST_WORKERS ?= 2

.DEFAULT_GOAL := help
.PHONY: help sync lint public-text fmt types test test-db drift check migrate evals gen-client api worker dev-token

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-11s %s\n", $$1, $$2}'

sync: ## Install every workspace member exactly as locked
	$(UV) sync --locked

lint: public-text ## ruff lint and format check (and the public-text check)
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

# The public repo describes the product, not how it is built: no build-machine names and no
# paths into private notes (AGENTS.md). The patterns are written so they never match this file.
public-text: ## Refuse build-machine names and private-note paths in tracked files
	@if git grep -n -I -E '\bMX[0-3]\b|vitrina-b[r]ain|research/(OPEN[.]md|findings/)' -- . ':!evals/cassettes'; then \
		echo "public-text: the lines above name build machines or private notes; reword them"; exit 1; fi

migrate: ## Upgrade DB_SCHEMA on DATABASE_URL to the latest migration
	$(UV) run alembic upgrade head

evals: ## Replay evals in this process (fails on an invented price or a consent error)
	$(UV) run python -m vitrina_evals --suite consent_scheduler --out tmp/evals
	$(UV) run python -m vitrina_evals --suite price_validator --out tmp/evals
	$(UV) run python -m vitrina_evals --suite conversations_es --out tmp/evals

gen-client: ## TypeScript client from contracts/openapi.yaml (T013)
	./scripts/gen-client

api: ## Run the API with reload on :8000
	$(UV) run uvicorn vitrina_api.main:create_app --factory --reload --port 8000

worker: ## Run the job worker
	$(UV) run vitrina-worker

dev-token: ## Local seller token without Supabase (SUB=dev-seller, SELLER=<uuid>)
	@$(UV) run python scripts/dev-token.py --sub $(or $(SUB),dev-seller) $(if $(SELLER),--seller $(SELLER))
