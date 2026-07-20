# DWG-Agent Enterprise CAD Processing Platform

<img src="frontend/public/logo.png" alt="DWG-Agent" width="120" />

[简体中文](README.md) | [English](README_EN.md)

**Delivery tier: v0.1 technical preview. Current documentation baseline: July 21, 2026.** This tier is intended for technical evaluation and continued development; it does not represent production readiness. Runtime facts are governed by the current code, migrations, configuration, and [verification evidence](docs/verification/current.md). The [development guide](docs/guides/development.md) provides the first-install path, the [implementation status](docs/architecture/implementation-status.md) records residual risks, and the [Enterprise Platform Technical Specification](docs/architecture/platform-specification.md) defines normative boundaries. The repository maintains Chinese project documentation only.

> [!IMPORTANT]
> This README describes only what is present in the repository today. Placeholder directories, disabled feature flags, and unconfigured infrastructure are not presented as delivered capabilities. Detailed project documentation is maintained **in Chinese only** under [docs/](docs/README.md); this English README provides a project-level overview.

## 🧭 Layered Guide

Choose a path based on what you need:

| Goal | Recommended path |
|---|---|
| Understand what the platform can and cannot do | [Platform Status](#-platform-status) → [Scope Boundaries](#-scope-boundaries) |
| Understand deployment and communication | [System Architecture](#️-system-architecture) → [Local Setup](#-local-setup) |
| See which processing pipelines can be enabled | [Processing Capabilities](#-processing-capabilities) → [Workflow boundaries](docs/architecture/workflow.md) |
| Develop and validate changes | [Development and Verification](#-development-and-verification) → [Development guide](docs/guides/development.md) |
| Deploy and operate the platform | [Compose Deployment](#compose-deployment) → [Deployment guide](docs/guides/deployment.md) → [Operations guide](docs/guides/operations.md) |

Status markers: **✅ Implemented** · **⚠️ Conditionally available** · **⏸️ Disabled/placeholder** · **❌ Out of scope**

## 🚦 Platform Status

### Core Capabilities

| Area | Status | Current implementation | Boundary |
|---|---|---|---|
| Web and API | ✅ | React administration UI, Nginx gateway, 96 OpenAPI paths, and 115 operations | Production disables `/docs`, `/redoc`, and `/openapi.json`; Nginx is not an authorization boundary |
| Data | ✅ | MySQL 8.x is the only runtime source of business truth; Alembic manages 28 model tables, and Celery creates 8 broker/result tables on demand | A migrated empty database has 29 tables; up to 37 after all Celery runtime tables exist; SQLite is used only by pytest |
| Asynchronous jobs | ✅ | Celery uses the MySQL SQLAlchemy transport and MySQL result backend | Suitable for the current bounded worker topology; not equivalent to a high-throughput message broker |
| Storage | ✅ | Local/MinIO inventory, transfer ledger, asynchronous consistency scans, DXF preview lifecycle, and four safe remediation actions | MySQL stores registrations and the storage layer stores bytes; cross-system changes use saga/compensation rather than a claimed single ACID transaction |
| Data console | ✅ | Overview, file registrations, storage objects, transfer history, and consistency views | Administrators can scan/remediate; auditors are read-only and may run previews; permanent deletion is irreversible and requires confirmation |
| Excel Final console | ✅ | Permission-filtered overview, job monitoring, cross-batch search, weight-ratio queries, batch/part/component pagination, result preview, and URL state restoration | Upload/job creation uses database idempotency keys; health bars show actual database/storage backends; historical data remains available while the pipeline is disabled |

### Orchestration and Extension Capabilities

| Area | Status | Current implementation | Boundary |
|---|---|---|---|
| Generic workflows | ⚠️ | `workflow_runs/stage_runs/artifacts`, project permissions, state transitions, audit APIs, and a Production Workflow UI | Provides persisted, visible manual orchestration only; it does not automatically create Excel Final jobs or attach artifacts |
| Conversion pipelines | ⚠️ | Service paths for report, DWG → DXF, DXF → DWG, DXF → Excel, and Excel Final; authenticated SVG preview for DXF | All four business pipelines are disabled by default and depend on ODA, Stage integrity, or the handbook database; online preview has independent size/complexity limits |
| Agent | ⏸️ | API, model, and permission boundaries remain | No further implementation is planned; `tasks_agent.py` remains a placeholder and `AGENT_ENABLED=false` |
| Windows CAD worker | ⏸️ | Drawing metadata and format-conversion boundaries remain | Component extraction, classification, plate splitting, left/right feed handling, interactive CAD, and the CAD Worker are outside the current delivery scope |
| Redis/Valkey | ❌ | Not part of the current runtime | Business state, SSE, token revocation, Agent memory, and Celery broker/results use MySQL directly |

## 🏗️ System Architecture

### Runtime Topology

```text
Browser
  -> Nginx
     -> React SPA
     -> FastAPI
        -> MySQL (business data + Celery broker/result tables)
        -> Local FS or MinIO
Celery workers (no inbound listening ports)
  -> MySQL:3306 to claim messages and conditionally update Job/JobStep
  -> Local FS or MinIO to read sources and write results
  -> Isolated Stage / ODA subprocesses
```

### Ports and Networking

| Mode | User entry point | FastAPI | MySQL / MinIO |
|---|---|---|---|
| Local development | Vite at `127.0.0.1:5173` or Nginx at `127.0.0.1:8080` | `127.0.0.1:8010` | MySQL at `127.0.0.1:3306`; MinIO is optional |
| Compose | Host HTTP `:80` → Nginx container `:8080` | Internal `backend-api:8010` only | Internal network only; no host ports are published |

> [!WARNING]
> The current Compose stack publishes **HTTP only**. It maps host `${HTTP_PORT:-80}` to Nginx container port `8080`; it does **not** publish port 443 or provide TLS. Before exposing the platform publicly, add certificates, HTTPS redirects, HSTS, renewal, and real browser/handshake verification at a controlled ingress. Network isolation and security headers are not substitutes for transport encryption.

## 🧩 Processing Capabilities

### Pipeline Matrix

| Pipeline / queue | Status | Default flag | Runtime requirements |
|---|---|---|---|
| framework smoke / `report` | ✅ Runnable framework task | Core worker starts by default | MySQL broker/results and storage must be available; this does not mean the report Agent exists |
| DWG → DXF / `dxf` | ⚠️ Service, task, tests, and ODA adapter exist | `DXF_PIPELINE_ENABLED=false` | ODA File Converter, a headless X environment, and a validated source DWG |
| DXF → DWG / `dxf2dwg` | ⚠️ Service, task, tests, and ODA adapter exist | `DXF2DWG_PIPELINE_ENABLED=false` | Same as above, plus a valid DXF input |
| DXF → Excel / `dxf2excel` | ⚠️ Stage source, platform service/task, and tests are tracked in the parent repository | `DXF2EXCEL_PIPELINE_ENABLED=false` | Valid DXF, Stage locked dependencies; built-in tests cover decoding only; real batches still require external corpus acceptance |
| Excel Final / `excel_final` | ⚠️ Backend adapter, isolated subprocess, relational import, and Stage tests exist | `EXCEL_FINAL_PIPELINE_ENABLED=false` | Valid Tekla/initial-sheet schema, read-only `hardware_handbook` database, and sufficient timeout |
| Agent / `agent` | ⏸️ API and persistence boundaries exist; task is an empty placeholder | `AGENT_ENABLED=false` | Delivery conditions are not met |
| Windows / `cad` | ⏸️ `windows/` separates external process contracts; task remains a placeholder | `CAD_WORKER_ENABLED=false` | Delivery conditions are not met; Compose has no `worker-cad` service |

### Job Consistency

Each execution generation is identified by `(job_id, attempt)`. A retry increments `attempt`; worker claims, progress updates, terminal transitions, cancellations, and compensation writes must match both the current status and attempt. This prevents stale messages or workers from overwriting a newer run. SSE polls MySQL and emits the authoritative snapshot for the current attempt; it does not provide event-ID history replay.

### Workflow Boundary

Generic workflows use `workflow_runs → workflow_stage_runs → workflow_artifacts` to model project-level business stages and artifact versions. Public capabilities include create, list, detail, start, manual stage confirmation, cancellation, and frontend display.

The public workflow routes bind Job attempts, synchronize authoritative state, and attach artifacts for implemented stages. Placeholder and external stages still require explicit handoff artifacts and do not claim an automated production loop. See the [Linux production workflow](docs/architecture/workflow.md).

## 🎯 Scope Boundaries

### Areas Still Being Developed

- Projects, files, and format conversion;
- Excel Final and generic workflows;
- Jobs, review, permissions, and auditing;
- Deployment and operations framework.

### Outside the Current Delivery Scope

- CAD drawing component extraction, automatic classification, automatic/interactive plate splitting, and left/right feed algorithms;
- ZWCAD secondary development and the Windows CAD Worker;
- Productized Agent behavior, model calls, MCP tool orchestration, and Agent memory.

Related routes, models, configuration, or placeholder directories remain only as historical or compatibility boundaries. Their presence does not indicate planned delivery.

## ⚠️ Known Limitations

1. Compose currently provides HTTP only and does not publish `443`; TLS ingress, certificate lifecycle, and HTTPS verification are not implemented.
2. Backups, retention, monitoring and alerting, centralized logs, and disaster-recovery drills are not automated. Documented procedures are operational baselines, not deployed services.
3. The MySQL SQL transport lacks the throughput, routing, and remote-control capabilities of brokers such as RabbitMQ. MySQL must remain the source of business truth if the broker is replaced or scaled out.
4. Conversion depends on proprietary ODA binaries, licensing, and runtime setup. Passing unit tests does not prove compatibility with every real DWG/DXF version.
5. The repository has not declared a LICENSE. Until the project lead confirms authorization, third-party licensing, and sample-data distribution scope, the code may only be used as an internal technical preview and must not be assumed to be open-source or redistributable.

## 🚀 Local Setup

### Prerequisites

- Python 3.12 and `uv`;
- Node.js and npm;
- MySQL 8.x;
- Stage dependencies required by the pipelines you enable.

### Start the Development Environment

```bash
cp .env.example .env
cp .env.example backend/.env
# Replace passwords and the JWT secret; MYSQL_* must match in both files.

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

`start-dev.sh` starts workers for the five implemented queues (excluding agent/cad), FastAPI on `8010`, and Vite. `start-all.sh` also builds the frontend and starts local Nginx on `8080`. A worker may remain healthy while a feature flag is disabled, but the corresponding API will reject new jobs.

To reuse MySQL/MinIO from containers while hot-reloading the API, run:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile workers up --build
```

The development override publishes only `127.0.0.1:8010` to the host; it does not publish MySQL or MinIO.

### Common Management Commands

```bash
bash scripts/status.sh
bash scripts/stop-all.sh
bash scripts/db.sh status
```

### Compose Deployment

After accepting the current HTTP-only, internal technical preview, and external Stage dependency boundaries:

```bash
cp .env.docker.example .env.docker
# Replace every CHANGE_ME_* value; do not commit .env.docker.
npm --prefix frontend ci
npm --prefix frontend run build

docker compose config --quiet
docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

The core set is `nginx/backend-api/mysql/minio/worker-report`. The `workers` profile adds conversion workers and the placeholder `worker-agent`. A healthy `worker-agent` means only that the Celery process connected to the broker; it does not mean Agent tasks are implemented.

## 🧪 Development and Verification

### Documentation, Static Checks, and Backend Tests

```bash
make docs-check
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ..
```

### Stages, Database, and Infrastructure Contracts

```bash
cd Stages/dwg2dxf && uv run pytest -q && cd ../..
cd Stages/dxf2dwg && uv run pytest -q && cd ../..
cd Stages/dxf2excel && uv run pytest -q && cd ../..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
bash scripts/db.sh migration-test
bash infra/verification/verify.sh
docker compose config --quiet
```

### Frontend Build and Browser Tests

```bash
cd frontend
npm run build
npx playwright test
```

These verification layers are not interchangeable: SQLite pytest checks business logic, `migration-test` checks an empty MySQL schema, `infra/verification/verify.sh` checks static and active infrastructure contracts, and Playwright checks browser interactions.

A complete release acceptance still requires real MySQL, Celery, MinIO, and valid sample files to exercise upload, processing, retry, SSE, signed download, storage interruption, and recovery end to end. See [current verification evidence](docs/verification/current.md).

## 🗂️ Repository Layout

```text
backend/        FastAPI, SQLAlchemy, Alembic, Celery, storage adapters, and pytest
frontend/       React administration UI, API client, and Playwright
Stages/         Independent CAD/Excel processing stages; Python Stage source is tracked, external binaries/corpus managed separately
agents/         Placeholder directories for undelivered Agents
windows/        Node Agent, CAM Runner, SinoCAM Adapter, and protocol contracts
infra/          Gateway, database, storage, messaging target, operations, verification
scripts/        Local lifecycle, database, and documentation tools
docs/           The only maintained detailed documentation set; written in Chinese
third_parts/    External/upstream projects; not automatically delivered platform capabilities
```

## 📚 Documentation

| Category | Documents |
|---|---|
| Overview | [Implementation status](docs/architecture/implementation-status.md) · [Verification evidence](docs/verification/current.md) · [Documentation index](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) |
| Specification | [Enterprise Platform Technical Specification](docs/architecture/platform-specification.md) |
| Design | [Architecture](docs/architecture/overview.md) · [Database](docs/reference/database.md) · [Linux production workflow](docs/architecture/workflow.md) |
| Development | [Development guide](docs/guides/development.md) · [API](docs/reference/api.md) · [Configuration](docs/reference/configuration.md) |
| Pipelines | [Workflow boundaries](docs/architecture/workflow.md) · [Verification evidence](docs/verification/current.md) |
| Delivery | [Deployment](docs/guides/deployment.md) · [Operations](docs/guides/operations.md) · [Security](docs/guides/security.md) · [Implementation gaps](docs/architecture/implementation-status.md) |

After changing routes, run `make docs-generate` to regenerate `docs/reference/api.md`. Before committing, run `make docs-check`.
