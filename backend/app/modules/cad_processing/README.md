# CAD processing module

## Responsibility

This module orchestrates DWG-to-DXF, DXF-to-DWG and DXF-to-material-workbook
Stages. It also inspects DXF structure and renders registered SVG previews. It
does not own HTTP authorization, file rows, Job rows or Stage algorithms.

## Boundaries

- `interface.py` is the only import path for other business modules.
- `execution.py` contains shared attempt-aware worker primitives only.
- `batching.py` contains only the common ODA directory-call adapter.
- `dwg_to_dxf/` and `dxf_to_dwg/` each separate `contracts.py`,
  `versions.py`, `persistence.py`, single-job `execution.py` and `batch.py`.
- `dxf_to_excel/` separates batch lookup/download in `staging.py`, workbook
  registration in `persistence.py` and Job sequencing in `execution.py`.
- `preview_rendering.py` is pure bounded DXF inspection/rendering;
  `preview.py` owns cache lookup and the Files transfer-ledger integration.
- `statistics.py` contains DXF entity counting shared by conversion fidelity
  reporting, not a second persistence path.
- Preview bytes remain owned by the files registry and its transfer saga.
- `tasks.py` retains the historical Celery task names and queue routing.
- `Stages/*` remain independent versioned CLI products and are never copied
  into this module.

## Traceable layout

```text
cad_processing/
├── interface.py             cross-domain API
├── execution.py             shared attempt/source/step primitives
├── batching.py              shared ODA directory invocation
├── preview_rendering.py     bounded parse and SVG renderer
├── preview.py               cache, file row and transfer ledger
├── statistics.py            entity statistics
├── tasks.py                 five stable Celery task names
├── dwg_to_dxf/              source version -> DXF registration
├── dxf_to_dwg/              provenance/header version -> DWG registration
└── dxf_to_excel/            registered DXF batch -> material workbook
```

Each direction README records its source, output, Stage, queue and stable task
name. ORM ownership stays in Files and Jobs; this module owns no table.

The ODA-backed paths are feature-gated and still require deployment/runtime and
representative-sample validation. Directory organization does not change that
production limitation.
