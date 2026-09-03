.PHONY: help install test test-sdk test-server test-ts test-ui lint fmt interop bench docs docs-build demo demo-live version release up down clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install everything for local development
	pip install -e ./sdk
	pip install -e "./server[dev]"
	cd sdk-ts && npm install
	cd ui && npm install

test: test-sdk test-server test-ts test-ui interop  ## Run every test suite

test-sdk:  ## Python SDK tests
	cd sdk && python test_sdk.py

test-server:  ## Server tests
	cd server && python -m pytest tests -q

test-ts:  ## TypeScript SDK tests
	cd sdk-ts && npm test

test-ui:  ## UI component tests
	cd ui && npm test

bench:  ## Measure tracing overhead
	python scripts/benchmark.py

interop:  ## Verify both SDKs produce the same DAG
	python scripts/interop_check.py

lint:  ## Check lint, formatting, version sync, and docs links
	ruff check .
	ruff format --check .
	python scripts/release.py check
	python scripts/check_docs.py

docs:  ## Serve the documentation site locally
	mkdocs serve

docs-build:  ## Build the docs site, failing on a broken link
	mkdocs build --strict

version:  ## Show every version string
	python scripts/release.py check

release:  ## Show the release checklist (make release V=0.4.0)
	python scripts/release.py plan $(V)

fmt:  ## Fix lint and format
	ruff check . --fix
	ruff format .

demo:  ## Seed a running server with realistic demo data
	python scripts/seed_demo.py

demo-live:  ## Seed, then stream one run in real time
	python scripts/seed_demo.py --live

up:  ## Start Postgres, server, and UI
	docker compose up --build

down:  ## Stop everything
	docker compose down

clean:  ## Remove build artifacts
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf sdk/agentlens.egg-info sdk/dist sdk-ts/dist ui/dist **/*.db
