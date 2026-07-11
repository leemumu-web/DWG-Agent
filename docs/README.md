# DWG-Agent Platform Documentation

> Chinese index: [zh/README.md](zh/README.md)

These documents describe the implementation audited from `main@d178fcf` on 2026-07-11 before the current documentation edits. They distinguish implemented code, default enablement, external prerequisites, verified evidence, and future work.

## Reading Order

1. [Root README](../README.md) for status, startup, and known blockers.
2. [Enterprise technical specification](../DWG-Agent企业平台技术规范.md) for normative boundaries.
3. [Architecture](architecture.md) and [processing pipelines](processing-pipelines.md) for request and worker paths.
4. [Configuration](configuration.md), [deployment](deployment.md), and [operations](operations.md) for environment work.
5. [Security](security.md), [database](database.md), and [workflow verification](workflow-verification.md) before release.

## Document Map

| Document | Purpose | Source of truth / update trigger |
|---|---|---|
| [API reference](api.md) | Route inventory and common API conventions | Generated from FastAPI OpenAPI with `make docs-generate` |
| [Architecture](architecture.md) | Component ownership, request paths, state and storage boundaries | Application/services, Nginx, Celery configuration |
| [Configuration](configuration.md) | Settings, defaults, precedence, secrets and flags | `config.py`, environment templates, scripts |
| [Database](database.md) | 22 business tables, runtime tables, migrations, seed and data protection | SQLAlchemy models, Alembic, `db.sh` |
| [Deployment](deployment.md) | Local/Compose topology and build constraints | Compose, Dockerfile, Nginx, start scripts |
| [Development](development.md) | Repository workflow, tests and change rules | Package manifests, tests, Makefile |
| [Operations](operations.md) | Health, logs, incidents, backup/restore and release runbook | Runtime scripts and current operational gaps |
| [Processing pipelines](processing-pipelines.md) | Inputs, steps, queues, outputs and enabling conditions | Pipeline services/tasks and Stage projects |
| [Security](security.md) | Authentication, authorization, file, job and audit boundaries | Security/dependency code and adversarial tests |
| [Workflow verification](workflow-verification.md) | Repeatable gates, E2E scenarios and dated evidence | Actual test runs; rerun after relevant changes |
| [Roadmap](roadmap.md) | Explicit incomplete work and completion criteria | Known implementation/deployment gaps |

## Ownership Boundary

The bilingual contract covers every `docs/*.md` file and its same-name `docs/zh/*.md` mirror. The root Chinese README is paired with this English documentation entry and the English detailed set, rather than duplicated line-for-line.

Component READMEs under `backend/`, `frontend/`, `infra/`, `agents/`, `cad-worker/`, and tracked `Stages/` describe local ownership. Algorithm handbooks such as `Stages/excel_final/PROCESS.md` may remain domain-language documents. `third_parts/` documentation belongs to upstream/vendored projects and is not rewritten as platform capability.

`Stages/dxf2excel` is currently a broken gitlink rather than parent-repository content. Its populated local README is not a tracked platform document and cannot be part of the documentation gate until repository ownership is repaired.

## Truthfulness Rules

- A directory, route, queue, environment variable, or healthy process does not by itself prove a feature is delivered.
- State whether code exists, the flag default, required external dependencies, and the level/date of verification.
- Current Compose is HTTP only; do not present the inactive `443:8443` mapping as TLS.
- Production disables runtime OpenAPI/Swagger/ReDoc; generated API files are the stable reference.
- Redis/Valkey is not a current component. Historical migration references must be labeled historical.
- Backup, monitoring, retention, Agent, CAD worker, and clean-clone `dxf2excel` support remain incomplete.

## Update Workflow

```bash
# Route changes
make docs-generate

# Every documentation change
make docs-check

# Relevant implementation gates
cd backend && uv run pytest -q && uv run alembic check
cd ../frontend && npm run build
```

Update each English/Chinese pair in the same change. Preserve paths, endpoints, environment variable names, status names, migration revisions, commands, table shape, and heading structure across languages. A dated verification statement must describe its environment and remains historical evidence after later changes.
