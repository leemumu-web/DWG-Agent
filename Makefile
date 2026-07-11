.PHONY: backend-install backend-init-db backend-dev backend-check frontend-install frontend-dev docs-generate docs-check db-start db-setup db-init db-migrate db-migration-test db-check db-status db-shell db-logs start-all start-dev status stop-all tree

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
	cd backend && uv run python ../scripts/generate_api_docs.py

docs-check:
	cd backend && uv run python ../scripts/check_docs.py

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
