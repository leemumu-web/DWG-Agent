# DWG-Agent Platform — Documentation

> 🌐 **Language:** **English** | [中文](zh/README.md)
>
> Handover documentation for the DWG-Agent enterprise CAD processing platform.
> Every document is kept in sync with the code and mirrored 1:1 in [`zh/`](zh/) (Chinese).
> **Spec authority:** [`../DWG-Agent企业平台技术规范.md`](../DWG-Agent企业平台技术规范.md).

## Index

| Document | Contents |
|----------|----------|
| [architecture.md](architecture.md) | System overview, physical topology, layered architecture, data flow, RBAC model, storage, implementation-status matrix, feature-flag inventory. |
| [api.md](api.md) | Full REST API reference — all `/api/v1` endpoints, authentication, unified response/error envelope, pagination, status codes, roles, job-status state machine, pipeline constants, feature flags. |
| [database.md](database.md) | Engine & pool configuration, complete table catalog, entity relationships, Alembic migrations, seed data, backup/restore. |
| [deployment.md](deployment.md) | Prerequisites, 5-minute quick start, local-dev setup, Docker Compose topology, environment-variable reference, Nginx/MySQL/MinIO/Celery SQL transport/ODA configuration. |
| [development.md](development.md) | Repository walkthrough, backend & frontend workflows, testing strategy, code conventions, dependency management, common pitfalls. |
| [security.md](security.md) | Authentication flow, RBAC model, API & file security measures, pentest-findings resolution, production security checklist, audit-log coverage. |
| [roadmap.md](roadmap.md) | Current integrated baseline, reliability/Agent/CAD/operations priorities, acceptance gates, and explicit non-goals. |
| [workflow-verification.md](workflow-verification.md) | End-to-end full-stack verification walkthrough exercising the platform against the live API. |

## Conventions

- All paths are **relative to the repository root** — no machine-specific absolute paths.
- English lives at `docs/`; the Chinese mirror lives at `docs/zh/` with identical structure (headings, tables, code blocks). Only prose language differs; technical tokens (endpoint paths, env vars, code, commands) are identical in both.
- When you change an interface, update the relevant English document **and** its `zh/` counterpart in the same commit.
