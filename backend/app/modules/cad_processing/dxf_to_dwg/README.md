# DXF to DWG direction

- Input: one registered DXF referenced by `Job.params_json.file_id`.
- Output: one registered DWG plus an `AnalysisResult` for the active attempt.
- Stage: `Stages/dxf2dwg` (`dxf-converter 0.1.0`).
- Queue: `dxf2dwg`.
- Stable task names: `app.workers.tasks_dxf2dwg.convert_dxf_to_dwg` and
  `app.workers.tasks_dxf2dwg.convert_dxf_to_dwg_batch`.

`versions.py` first resolves original DWG provenance from a prior
DWG-to-DXF `AnalysisResult`, validates that value, then falls back to the DXF
`$ACADVER` header and finally the configured default. `persistence.py` owns
DWG/Files registration. Single and batch attempt orchestration remain separate.

The feature flag and ODA runtime/sample validation remain production gates.
