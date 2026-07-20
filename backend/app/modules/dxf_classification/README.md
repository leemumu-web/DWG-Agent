# DXF classification module

## Responsibility

This module invokes Steel DXF Classifier 1.1.0 for a frozen production input,
registers every routed DXF plus JSON/CSV reports in object storage and MySQL,
and exposes the latest classification ledger to Workflow HTTP presentation.

It owns `dxf_classification_runs` and `dxf_classification_items`. File bytes,
Jobs and workflows remain authoritative in their respective modules.

## Current boundary

- `adapter.py` owns the Classifier CLI, schema, exit-code and filename contract.
- `persistence.py` owns source/output ledger operations.
- `execution.py` owns attempt-aware Job and workflow orchestration.
- `interface.py` is the only supported cross-domain import path.
- `tasks.py` preserves the public Celery task name and queue.
- `models.py` owns the run/item ORM ledger; `schemas.py` and `presentation.py`
  convert that ledger to the Workflow response without moving HTTP ownership.

```text
dxf_classification/
├── interface.py       cross-domain API
├── adapter.py         1.1 CLI, schema, exit code, route and filename contract
├── persistence.py     frozen sources, files, run/item/result ledgers
├── execution.py       attempt-aware Job and Workflow sequencing
├── models.py          two owned MySQL tables
├── schemas.py         typed response records
├── presentation.py    ORM-to-response mapping
└── tasks.py           stable public task name and queue binding
```

Until the Workflow domain moves in its dedicated refactor, execution retains a
documented transitional dependency on the old workflow input/artifact services.
The classifier performs preprocessing and classification only; automatic plate
splitting remains a later explicit stage, not an implied implementation.
