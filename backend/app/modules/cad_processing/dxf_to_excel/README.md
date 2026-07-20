# DXF to material workbook direction

- Input: every live registered `.dxf` sharing `Job.params_json.batch_name`.
- Output: one registered `.xlsx` material workbook plus `AnalysisResult`.
- Stage: `Stages/dxf2excel` (`dxf2excel 0.1.0`).
- Queue: `dxf2excel`.
- Stable task name: `app.workers.tasks_dxf2excel.extract_dxf_to_excel`.

`staging.py` is the only batch membership and Local/MinIO download path.
`execution.py` invokes the Stage per file, publishes bounded progress and writes
the combined workbook. `persistence.py` owns terminal workbook/result
registration. `contracts.py` keeps error/output metadata consistent.

This first workbook remains in CAD processing because its source domain is a
DXF batch. Excel Final and its relational import belong to `excel_processing`.
