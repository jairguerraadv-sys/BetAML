.PHONY: up down build migrate seed logs test

## Start all services (build if needed)
up:
	cd infra && docker compose up -d --build

## Stop all services
down:
	cd infra && docker compose down

## Build images without starting
build:
	cd infra && docker compose build

## Run database migrations
migrate:
	cd infra && docker compose exec api alembic upgrade head

## Seed the database (creates tenants, users, rules, synthetic data)
seed:
	cd infra && docker compose run --rm seed

## Tail logs for all services
logs:
	cd infra && docker compose logs -f

## Run unit tests locally (requires: pip install -r tests/requirements.txt)
test:
	cd tests && python -m pytest unit/ -v

## Full local setup: start → migrate → seed
setup: up
	@echo "Waiting for services to be healthy..."
	@sleep 30
	$(MAKE) migrate
	$(MAKE) seed
	@echo ""
	@echo "BetAML is ready!"
	@echo "  Frontend:         http://localhost:3000"
	@echo "  API (Swagger):    http://localhost:8000/docs"
	@echo "  ML Service:       http://localhost:8001/docs"
	@echo "  Redpanda Console: http://localhost:8080"
	@echo "  MinIO Console:    http://localhost:9001"
	@echo ""
	@echo "Login: admin@operadora.com / Admin123!"
