.PHONY: install dev test lint format typecheck setup-db set-webhook

install:
	uv sync --group dev

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy app/

setup-db:
	uv run python scripts/setup_db.py

set-webhook:
	uv run python scripts/set_webhook.py
