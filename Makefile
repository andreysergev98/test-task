.PHONY: up down logs test test-integration lint format typecheck migrate

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api consumer

test:
	python -m pytest -m "not integration" --cov --cov-report=term-missing

test-integration:
	@set +e; \
	docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests; \
	compose_status=$$?; \
	docker compose -f compose.test.yaml down --volumes --remove-orphans; \
	cleanup_status=$$?; \
	if [ $$compose_status -ne 0 ]; then exit $$compose_status; fi; \
	exit $$cleanup_status

lint:
	ruff check src tests migrations examples
	ruff format --check src tests migrations examples

format:
	ruff check --fix src tests migrations examples
	ruff format src tests migrations examples

typecheck:
	mypy src

migrate:
	alembic upgrade head
