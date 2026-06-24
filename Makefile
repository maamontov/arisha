.PHONY: help install sync dev build up down restart logs ps test test-unit test-e2e test-cov lint format check smoke clean migrate revision reset-db

help:
	@echo "Arisha Payments — available commands"
	@echo ""
	@echo "  make install      Install all dependencies (including dev) via uv"
	@echo "  make sync         Sync dependencies to current lockfile"
	@echo "  make dev          Run API locally with hot-reload"
	@echo "  make build        Build Docker images"
	@echo "  make up           Start docker-compose stack (detached)"
	@echo "  make down         Stop docker-compose stack"
	@echo "  make restart      Restart services"
	@echo "  make logs         Tail logs from all services"
	@echo "  make ps           List running services"
	@echo "  make test         Run all pytest tests (unit + e2e)"
	@echo "  make test-unit    Run unit tests only (no e2e)"
	@echo "  make test-e2e     Run end-to-end tests (requires stack up)"
	@echo "  make test-cov     Run pytest with coverage"
	@echo "  make lint         Run ruff check"
	@echo "  make format       Run ruff format"
	@echo "  make check        Run ruff check + format check"
	@echo "  make smoke        Quick sanity check (compile, lint, format, compose)"
	@echo "  make migrate      Apply Alembic migrations"
	@echo "  make revision     Create new Alembic revision (msg=...)"
	@echo "  make reset-db     Drop and recreate DB (DESTRUCTIVE)"
	@echo "  make clean        Remove caches and volumes"

install:
	uv sync --all-extras

sync:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

build:
	docker compose build

up:
	docker compose up -d
	@echo ""
	@echo "API:        http://localhost:$${API_PORT:-8000}/docs"
	@echo "RabbitMQ:   http://localhost:$${RABBITMQ_MANAGEMENT_PORT:-15672}"
	@echo "Receiver:   http://localhost:$${WEBHOOK_RECEIVER_PORT:-9000}"

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

test:
	uv run pytest -v

test-unit:
	uv run pytest -v --ignore=tests/test_e2e.py

test-e2e:
	uv run pytest tests/test_e2e.py -v

test-cov:
	uv run pytest --cov=app --cov=webhook_receiver --cov-report=term-missing --ignore=tests/test_e2e.py

lint:
	uv run ruff check .

format:
	uv run ruff format .

check: lint
	uv run ruff format --check .

smoke:
	@echo "==> Compiling Python files..."
	@python3 -m py_compile $(shell find . -name "*.py" -not -path "./.venv/*" -not -path "*/__pycache__/*") && echo "    OK"
	@echo "==> Validating docker-compose..."
	@docker compose config --quiet && echo "    OK"
	@if command -v ruff >/dev/null 2>&1; then \
		echo "==> Running ruff check..."; \
		ruff check . && echo "    OK"; \
		echo "==> Checking ruff format..."; \
		ruff format --check . && echo "    OK"; \
	else \
		echo "==> Skipping ruff (not installed; install via 'uv sync')"; \
	fi
	@echo ""
	@echo "All smoke checks passed."

migrate:
	uv run alembic upgrade head

revision:
	@if [ -z "$(msg)" ]; then echo "Usage: make revision msg=\"your message\""; exit 1; fi
	uv run alembic revision --autogenerate -m "$(msg)"

reset-db:
	docker compose down -v
	docker compose up -d postgres
	@echo "Waiting for postgres..."
	@sleep 5
	uv run alembic upgrade head

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov
	docker compose down -v 2>/dev/null || true
