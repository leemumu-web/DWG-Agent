.PHONY: backend-install backend-init-db backend-dev backend-check frontend-install frontend-dev tree

backend-install:
	cd backend && uv sync --locked

backend-init-db:
	cd backend && uv run python -m app.db.init_db

backend-dev:
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

backend-check:
	cd backend && python -m compileall app

frontend-install:
	cd frontend && npm ci

frontend-dev:
	cd frontend && npm run dev

tree:
	find . -maxdepth 4 -type f | sort
