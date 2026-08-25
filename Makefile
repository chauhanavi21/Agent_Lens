.PHONY: help install test test-sdk test-server test-ts lint fmt interop up down clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install everything for local development
	pip install -e ./sdk
	pip install -e "./server[dev]"
	cd sdk-ts && npm install
	cd ui && npm install

test: test-sdk test-server test-ts interop  ## Run every test suite

test-sdk:  ## Python SDK tests
	cd sdk && python test_sdk.py

test-server:  ## Server tests
	cd server && python -m pytest tests -q

test-ts:  ## TypeScript SDK tests
	cd sdk-ts && npm test

interop:  ## Verify both SDKs produce the same DAG
	python scripts/interop_check.py

lint:  ## Check lint and formatting
	ruff check .
	ruff format --check .

fmt:  ## Fix lint and format
	ruff check . --fix
	ruff format .

up:  ## Start Postgres, server, and UI
	docker compose up --build

down:  ## Stop everything
	docker compose down

clean:  ## Remove build artifacts
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf sdk/agentlens.egg-info sdk/dist sdk-ts/dist ui/dist **/*.db
