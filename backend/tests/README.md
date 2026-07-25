# Backend test layout

The backend suite mirrors the production ownership boundaries under
`app/modules` and the shared platform layers under `app/platform`.  Put a new
test in the directory that owns the behavior being asserted, not in the suite
root.

| Directory | Primary responsibility |
| --- | --- |
| `architecture/` | Dependency, ownership, runtime-snapshot and repository-layout contracts |
| `automation/` | Agent memory and deliberately disabled automation contracts |
| `cad_processing/` | DWG/DXF conversion, extraction and preview adapters |
| `contracts/` | Cross-process HTTP, frontend and documentation contracts |
| `dxf_classification/` | Steel DXF classifier integration |
| `dxf_splitting/` | Steel DXF Split 1.5.2 integration, review routing, storage and retry contracts |
| `excel_processing/` | Excel Final execution, persistence and retry semantics |
| `files/` | File registry, object storage and transfer consistency |
| `identity/` | Users, sessions, roles and token lifecycle |
| `infrastructure/` | Configuration, MySQL, Celery, deployment and operator scripts |
| `jobs/` | Job/result/review lifecycle, attempts, claims and event streams |
| `operations/` | Audit, control plane, archive, catalog and reconciliation use cases |
| `projects/` | Project, member, drawing and shared catalog services |
| `regression/` | Historical cross-domain audits and end-to-end regression probes |
| `security/` | Cross-cutting abuse cases and security invariants |
| `workflows/` | Production intake, state machine and orchestration behavior |
| `support/` | Reusable test-only paths, sessions and API builders; never test cases |

`conftest.py` remains at the root so its isolated SQLite fixture applies to the
entire recursive suite.  Cross-domain regression files stay intact when
splitting them would obscure their audit history.  Test modules must import
shared builders from `tests.support`, never from another `test_*.py` module.
Passing isolated tests must not be reported as MySQL, MinIO, ODA, browser or
Windows production acceptance unless that external layer was actually exercised.
