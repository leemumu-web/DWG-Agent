# DWG to DXF direction

- Input: one registered DWG referenced by `Job.params_json.file_id`.
- Output: one registered DXF plus an `AnalysisResult` for the active attempt.
- Stage: `Stages/dwg2dxf` (`dwg-converter 0.1.0`).
- Queue: `dxf`.
- Stable task names: `app.workers.tasks_dxf.convert_dwg_to_dxf` and
  `app.workers.tasks_dxf.convert_dwg_to_dxf_batch`.

`versions.py` maps the six-byte DWG header to the least-upgrading ODA version.
`persistence.py` owns MinIO/Files registration and AnalysisResult metadata.
`execution.py` owns one Job attempt; `batch.py` groups files by output version.
`contracts.py` centralizes failure and output metadata shared by those paths.

Successful automated tests do not prove the deployment ODA AppImage can open
every production drawing; representative DWG validation remains mandatory.
This direction must not accept a browser-uploaded DXF as a substitute for the
server-derived production input, and it does not own Workflow state changes.
