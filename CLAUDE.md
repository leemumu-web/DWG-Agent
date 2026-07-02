# DWG-Agent Platform — Agent Instructions

> **Spec authority:** `DWG-Agent企业平台技术规范.md` (repo root)
> (2455 lines, 25 sections, v1.0)
>
> Every design decision flows from this document. When in doubt, read the spec first.

---

## 1. What This Repository Is

**DWG-Agent** is an enterprise CAD intelligent processing platform for internal company use.
It accepts DWG drawings, understands natural-language tasks via an LLM Agent, and routes
work through two processing pipelines:

| Pipeline | Precision | Tech |
|----------|-----------|------|
| DWG → DXF → Python (ezdxf) | Low/medium | Linux Celery worker |
| DWG → ZWCAD API (C#) | High | Windows CAD Worker node |

The final system is a Docker Compose deployment: Nginx → FastAPI → MySQL/Redis/MinIO → Celery Workers.
**We are currently at Stage 1** — a no-Docker, local-dev skeleton that validates the RESTful API
and DB model layers end to end.

---

## 2. Repository Map

```
complete_framework/
├── DWG-Agent企业平台技术规范.md   ← CORE SPEC (read first for any design question)
├── README.md                      ← human-facing overview
├── .env.example                   ← local-dev env template
├── .env.docker.example            ← Docker Compose env template
│
├── backend/                       ← Python 3.12, uv, FastAPI
│   ├── pyproject.toml             ← deps + ruff config
│   ├── uv.lock                    ← COMMITTED — locked deps
│   ├── .python-version            ← 3.12
│   ├── app/
│   │   ├── main.py                ← FastAPI app, lifespan, CORS, exception handlers
│   │   ├── api/v1/                ← 12 route modules under /api/v1
│   │   │   └── router.py          ← central router assembly
│   │   ├── core/                  ← config, security, exceptions, redis_client, constants, logger
│   │   ├── db/                    ← base, session (engine + WAL pragmas), init_db (seeds)
│   │   ├── models/                ← 10 SQLAlchemy ORM models
│   │   ├── schemas/               ← Pydantic v2 request/response schemas
│   │   ├── services/              ← business logic (auth, user, job, storage, audit, redis_memory, cache_service)
│   │   ├── repositories/          ← PLACEHOLDER (empty __init__)
│   │   ├── agents/                ← PLACEHOLDER (agent prompts/factory/tool_registry)
│   │   ├── mcp_client/            ← PLACEHOLDER (MCP client + tool adapter)
│   │   ├── workers/               ← PLACEHOLDER (Celery tasks)
│   │   ├── storage/               ← PLACEHOLDER (base/local/minio stubs)
│   │   ├── integrations/zwcad/    ← PLACEHOLDER (ZWCAD worker client)
│   │   └── utils/                 ← path_utils, file_hash, time_utils
│   ├── tests/                     ← 153 tests (pytest + fakeredis + real Redis)
│   │   └── conftest.py            ← FakeRedis autouse fixture
│   ├── migrations/                ← Alembic (NO migrations yet — uses create_all)
│   └── var/                       ← runtime data (SQLite DB, uploaded files)
│
├── frontend/                      ← React 19 + TypeScript + Vite
│   ├── package.json               ← NO "latest" — all versions locked
│   ├── package-lock.json
│   └── src/
│       ├── api/                   ← 9 Axios API client modules
│       ├── app/                   ← router, layout, providers
│       ├── features/              ← 8 page modules (login, dashboard, projects, files, …)
│       ├── components/            ← 8 shared components
│       ├── stores/                ← Zustand (auth.store.ts)
│       └── types/                 ← TypeScript type definitions
│
├── docs/                          ← stage1-review.md, api.md
├── infra/                         ← DEPLOY CONFIG (nginx config, redis/ placeholder, Dockerfile later)
├── agents/                        ← PLACEHOLDER for future Agent definitions
├── cad-worker/                    ← PLACEHOLDER for Windows C# CAD Worker
├── scripts/                       ← 6 dev/ops shell scripts (lib.sh, start-dev, start-all, stop-all, status, init-db)
└── tests/                         ← PLACEHOLDER for E2E / integration tests
```

---

## 3. Key Conventions

### Paths — relative to repo root

- **All paths in docs, configs, and code MUST be relative to the repository root.** Never hardcode `/home/Creeken/...` or any user-specific absolute paths.
- `CLAUDE.md`, `.env.example`, `.env.docker.example`, `compose.yaml`, `README.md` — all at repo root, reference sub-paths as `backend/...`, `frontend/...`, `infra/...`
- Within `backend/`: `app/core/config.py` uses `Path("./var/storage")` (relative to CWD at runtime)
- Within `frontend/`: Vite config uses relative paths; `VITE_API_BASE_URL` is empty in dev (Vite proxy) and set via env in Docker
- Nginx configs: Docker uses `nginx.conf` (container paths `/etc/nginx/...`, `/usr/share/nginx/html`); local dev uses `nginx.local.conf` (started via `nginx -c $(pwd)/infra/nginx/nginx.local.conf` from repo root)
- **Exception:** `infra/nginx/nginx.local.conf` (local dev only, NOT used in Docker) contains hardcoded paths because nginx requires absolute paths for `error_log`/`pid`/`access_log`/`root` directives. A sed command to auto-replace is documented in the file header. Docker deployment uses `infra/nginx/nginx.conf` which uses container-relative paths.
- This convention ensures Docker builds and multi-developer workflows work without path edits.

### Language & Stack

- **Backend:** Python **3.12 only** (`>=3.12,<3.13`), `uv` for package management
- **Frontend:** React 19, TypeScript, Vite, Ant Design 6, TanStack Query, Zustand
- **Database:** SQLite for dev (`sqlite:///./var/app.db`), MySQL for production
- **MySQL config:** component fields (`mysql_host`/`mysql_port`/`mysql_database`/`mysql_user`/`mysql_password`) + computed `mysql_url` property, per spec §18
- **MySQL pool:** `pool_recycle=3600`, `pool_size=10`, `max_overflow=20` (only when `DATABASE_URL` starts with `mysql`)
- **ORM:** SQLAlchemy 2.x, synchronous session
- **Schema:** Pydantic v2 with `model_config = ConfigDict(from_attributes=True)`
- **Config:** pydantic-settings, `.env` file, `extra="ignore"`

### Code Style

- `ruff` with `select = ["E", "F", "I", "UP", "B"]`, line-length=100
- All files begin with `from __future__ import annotations`
- Type hints required (ruff UP rules enforce `X | None` over `Optional[X]`)
- Import style: `from collections.abc import Callable` (not `from typing import Callable`)
- End-of-file newline, no trailing whitespace

### API Patterns

- **All endpoints** return `{"data": …, "meta": {"request_id": …, "timestamp": …}}`
- **List endpoints** add `"pagination": {"page": …, "page_size": …, "total": …}`
- **Error responses** return `{"error": {"code": "…", "message": "…", "details": {}}, "meta": {…}}`
- Use `app.core.exceptions.AppHTTPException` for business errors (not bare HTTPException)
- Status codes per spec §7.2: 200/201/202/204 for success, proper HTTP semantic codes for errors
- Resource names are plural nouns
- Agent execution is `agent-runs` (kebab-case), not `agent/run`

### Architecture Rules (from Spec §6.2)

1. **API layer** — routes, param parsing, dependency injection, response wrapping. No business logic.
2. **Service layer** — business logic orchestration. No FastAPI Request dependency.
3. **Repository layer** — DB read/write encapsulation (currently inline; will be extracted).
4. **Worker tasks** — must call Services, never duplicate business logic.
5. **Agent code** — no direct DB or filesystem access; use tools or Service boundaries.
6. **File paths** — must pass through `app/utils/path_utils.py` validation.

### Redis

- **Server:** Valkey 9.1 (Redis-compatible), systemd `redis.service`, no password for local dev.
- **Client:** sync `redis-py` 5.x with `hiredis`. Lazy init, no crash on unavailable.
- **Testing:** dual-layer — `fakeredis[lua]` via `conftest.py` autouse fixture + real Redis integration (`test_redis_real.py`, auto-skipped when Redis unavailable).
- **Memory service:** `agent:memory:{session_id}` key, JSON list, TTL=7200s, max 20 messages.
- **Cache service:** `cache:{namespace}:{key}` pattern, all methods safe when Redis is down.
- **Celery URLs:** computed properties (`celery_broker_url` / `celery_result_backend`), auto-follow `redis_password`.
- At Stage 1, memory/cache are **infrastructure only** (validated by tests, not called by runtime).
- **Config:** `infra/redis/redis.conf` for Docker deployment (AOF, LRU, maxmemory 256mb).

### SQLite

- WAL journal mode (enabled per-connection via SQLAlchemy `Engine.connect` event)
- `PRAGMA foreign_keys=ON` — foreign key constraints are enforced
- `PRAGMA busy_timeout=5000` — 5s retry on lock contention
- `db_health()` returns `{"status": "ok", "message": "Database is reachable."}`

### Testing

```bash
cd backend
uv run ruff check app tests   # must pass
uv run pytest -q              # must pass (153 tests expected)
```

- Tests use `TestClient` from `fastapi.testclient`
- No real Redis — `conftest.py` injects `FakeRedis` via module-level monkeypatch
- No real HTTP — all tests are in-process
- Test DB is the same SQLite as dev; `init_db()` is called in tests that need seeded data
- MySQL fields tested via unit-level `Settings()` instantiation (no real MySQL server)

---

## 4. Critical Don'ts

1. **Don't implement without reading the spec** — `DWG-Agent企业平台技术规范.md` is the ground truth.
2. **Don't add to pyproject.toml without using `uv add`** — the lock file must stay consistent.
3. **Don't use `latest` in frontend `package.json`** — every dependency is pinned.
4. **Don't add async Redis or DB code** — the codebase is synchronous until Stage 2 Agent requires async.
5. **Don't enable Agent features** — `AGENT_ENABLED=false` must keep returning 503.
6. **Don't hardcode API URLs** — frontend uses `VITE_API_BASE_URL`, backend config is env-driven.
7. **Don't commit `.env` or `.env.docker`** — only `.env.example` and `.env.docker.example` are tracked.
8. **Don't put Docker/production configs in `app/`** — deployment configs go in `infra/`.
9. **Don't write business logic in route handlers** — push to services.
10. **Don't use `assert False` in tests** — use `raise AssertionError("message")` (ruff B011).

---

## 5. Key File Reference

| Purpose | Path |
|---------|------|
| **Core spec** | `DWG-Agent企业平台技术规范.md` |
| Stage 1 review | `docs/stage1-review.md` |
| Backend config | `backend/app/core/config.py` |
| FastAPI entry | `backend/app/main.py` |
| Route assembly | `backend/app/api/v1/router.py` |
| DB session + pragmas | `backend/app/db/session.py` |
| DB seed data | `backend/app/db/init_db.py` |
| Redis client | `backend/app/core/redis_client.py` |
| Redis memory | `backend/app/services/redis_memory.py` |
| Cache service | `backend/app/services/cache_service.py` |
| Auth service | `backend/app/services/auth_service.py` |
| Job stub worker | `backend/app/services/job_service.py` |
| File storage | `backend/app/services/storage_service.py` |
| MySQL config | `backend/app/core/config.py` (mysql_* fields + mysql_url property) |
| DB session + pool | `backend/app/db/session.py` |
| MySQL init script | `infra/mysql/init.sql` |
| Redis config | `infra/redis/redis.conf` |
| Compose infra | `compose.yaml` |
| Dockerfile | `backend/Dockerfile` (multi-stage, non-root, HEALTHCHECK) |
| Docker ignore | `backend/.dockerignore` |
| Dev scripts | `scripts/` (lib.sh + start-dev.sh / start-all.sh / stop-all.sh / status.sh / init_db.sh) |
| Test fixtures | `backend/tests/conftest.py` |
| Config tests | `backend/tests/test_config.py` (Redis + MySQL) |
| Session tests | `backend/tests/test_db_session.py` |
| Frontend router | `frontend/src/app/router.tsx` |
| Frontend API client | `frontend/src/api/client.ts` |
