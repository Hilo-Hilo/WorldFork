.PHONY: up down build logs migrate migrate-legacy revision seed test test-unit test-integration test-e2e test-all lint clean clean-data

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

migrate:
	docker compose exec api python -c "from app.db.models import Base; from app.db.session import engine; Base.metadata.create_all(bind=engine); import backend.app.models; from backend.app.models.base import Base as LegacyBase; names={'big_bang_runs','run_results','settings_branch_policy','settings_global','settings_model_routing','settings_provider','settings_rate_limit','settings_zep'}; LegacyBase.metadata.create_all(bind=engine, tables=[LegacyBase.metadata.tables[n] for n in names if n in LegacyBase.metadata.tables])"

migrate-legacy:
	docker compose exec api alembic -c infra/alembic.ini upgrade head

revision:
	docker compose exec api alembic -c infra/alembic.ini revision --autogenerate -m "$(m)"

seed:
	docker compose exec api python -m backend.app.scripts.seed

test:
	docker compose exec api pytest -n auto

# ---------------------------------------------------------------------------
# Local pytest entry points (no docker required) - match scripts/run_tests.sh.
# Run with `make test-unit`, etc.  `test-all` runs all three layers in CI order.
# ---------------------------------------------------------------------------

test-unit:
	.venv/bin/python -m pytest -c pyproject.toml backend/tests/*.py backend/tests/unit -n auto -q

test-integration:
	.venv/bin/python -m pytest -c pyproject.toml backend/tests/integration -q -n auto

test-e2e:
	.venv/bin/python -m pytest -c pyproject.toml backend/tests/e2e -q -m "not requires_broker and not live_openrouter and not live_zep"

test-all: test-unit test-integration test-e2e

lint:
	ruff check . && mypy backend/app

clean:
	docker compose down
	rm -rf .pytest_cache .mypy_cache .ruff_cache

clean-data:
	docker compose down -v
	rm -rf runs/*
