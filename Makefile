.PHONY: verify-quick verify-full architecture-check backend-install backend-init-db backend-dev backend-check frontend-install frontend-dev docs-generate docs-check db-start db-setup db-init db-migrate db-migration-test db-check db-status db-shell db-logs docker-check docker-build docker-up docker-up-workers docker-status docker-smoke docker-down start-all start-dev status stop-all tree

verify-quick:
	bash scripts/verify.sh quick

verify-full:
	bash scripts/verify.sh full

architecture-check:
	backend/.venv/bin/python scripts/architecture/snapshot_contracts.py --check
	backend/.venv/bin/python scripts/architecture/check_module_catalog.py

backend-install:
	cd backend && uv sync --locked

backend-init-db:
	bash scripts/db.sh init

backend-dev:
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010

backend-check:
	cd backend && python -m compileall app

frontend-install:
	cd frontend && npm ci

frontend-dev:
	cd frontend && npm run dev

docs-generate:
	cd backend && uv run python ../scripts/docs/generate_api.py

docs-check:
	cd backend && uv run python ../scripts/docs/check.py

db-start:
	bash scripts/db.sh start

db-setup:
	bash scripts/db.sh setup-user

db-init:
	bash scripts/db.sh init

db-migrate:
	bash scripts/db.sh migrate

db-migration-test:
	bash scripts/db.sh migration-test

db-check:
	bash scripts/db.sh check

db-status:
	bash scripts/db.sh status

db-shell:
	bash scripts/db.sh shell

db-logs:
	bash scripts/db.sh logs

docker-check:
	bash scripts/docker.sh check

docker-build:
	bash scripts/docker.sh build

docker-up:
	bash scripts/docker.sh up

docker-up-workers:
	bash scripts/docker.sh up-workers

docker-status:
	bash scripts/docker.sh status

docker-smoke:
	bash scripts/docker.sh smoke

docker-down:
	bash scripts/docker.sh down

start-all:
	bash scripts/start-all.sh

start-dev:
	bash scripts/start-dev.sh

status:
	bash scripts/status.sh

stop-all:
	bash scripts/stop-all.sh

tree:
	find . -maxdepth 4 -type f | sort
