.DEFAULT_GOAL := help
COMPOSE := docker compose -f docker-compose.dev.yml
AGY_TOKEN := $(HOME)/.gemini/antigravity-cli/antigravity-oauth-token

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'

check-auth:      ## Fail early if the agy token is missing
	@test -f "$(AGY_TOKEN)" || { \
	  echo "ERROR: $(AGY_TOKEN) not found."; \
	  echo "Run 'agy' once on this machine and sign in, then retry."; exit 1; }

dev: check-auth  ## Build and start the whole stack
	@test -f .env || cp .env.example .env
	$(COMPOSE) up --build

down:            ## Stop the stack
	$(COMPOSE) down

logs:            ## Tail all logs
	$(COMPOSE) logs -f

ps:              ## Show services
	$(COMPOSE) ps

shell:           ## Shell into the worker container
	$(COMPOSE) exec worker bash

gen-migration:   ## make gen-migration msg="describe_change"
	$(COMPOSE) run --rm worker alembic revision --autogenerate -m "$(msg)"

migrate:         ## Apply migrations
	$(COMPOSE) run --rm worker alembic upgrade head

verify:          ## Lint, format-check, typecheck, compile
	uv run ruff check . && uv run ruff format --check . && \
	uv run ty check && uv run python -m compileall -q src migrations

fmt:             ## Auto-fix and format
	uv run ruff check --fix . && uv run ruff format .

.PHONY: help check-auth dev down logs ps shell gen-migration migrate verify fmt
