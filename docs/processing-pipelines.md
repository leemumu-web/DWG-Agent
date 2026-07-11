# Processing Pipelines

> Chinese mirror: [zh/processing-pipelines.md](zh/processing-pipelines.md)

## Shared Job Contract

Every asynchronous pipeline uses the same control path:

```text
authenticated request
  -> validate feature flag, input, and resource access
  -> create Job(status=queued, attempt=N) and commit
  -> publish (job_id, attempt) to the routed MySQL queue
  -> worker atomically claims queued + expected attempt
  -> write attempt-scoped JobSteps and progress snapshots
  -> read source object -> execute Stage -> write result object
  -> persist file/result/domain metadata
  -> conditionally finish the same attempt
```

The API returns HTTP 202 after durable Job creation and dispatch. Dispatch failure conditionally marks only the still-queued attempt as failed. Workers must not create a separate correctness store or update a Job without matching its attempt.

## Capability Matrix

| Pipeline | Task / queue | Input | Output | Current boundary |
|---|---|---|---|---|
| Framework smoke | `local_stub` / `report` | Job parameters | JSON `AnalysisResult` | Implemented framework path, not an LLM report Agent |
| DWG -> DXF | `convert_dwg_to_dxf` / `dxf` | One stored DWG | Stored DXF + result row | Feature-gated; requires ODA and supported DWG header |
| DXF -> DWG | `convert_dxf_to_dwg` / `dxf2dwg` | One stored DXF | Stored DWG + result row | Feature-gated; requires ODA and valid DXF |
| DXF -> Excel | `extract_dxf_to_excel` / `dxf2excel` | Named batch of stored DXF files | XLSX + result row | Feature-gated; Stage gitlink is not reproducible from a clean clone |
| Excel Final | `process_excel_final` / `excel_final` | Stored `.xls`/`.xlsx` with supported content | Final XLSX + result + relational batch data | Feature-gated; requires handbook DB and supported schema |
| Agent | `agent` | Agent-run request | None | API/model boundary only; task module is a placeholder |
| Windows CAD | `cad` | Reserved | None | Config/task/directory placeholders; no deployed worker |

## DWG to DXF

`DXF_PIPELINE_ENABLED=true` permits `task_type=convert_dwg_to_dxf`. The service validates the stored file, stages it in a temporary directory, invokes the `dwg_converter` ODA adapter, stores a generated DXF in `dxf-derived`, creates an `AnalysisResult`, and completes the matching attempt.

Steps are `download_source_dwg`, `run_oda_convert`, and `persist_dxf_result`. The ODA timeout/retry settings bound the child process, but compatibility still depends on real source versions and ODA behavior. The tracked AppImage and unit tests do not establish universal DWG support or licensing rights.

## DXF to DWG

`DXF2DWG_PIPELINE_ENABLED=true` permits `task_type=convert_dxf_to_dwg`. The flow mirrors DWG -> DXF with steps `download_source_dxf`, `run_oda_convert_dxf`, and `persist_dwg_result`. It stores the derived object in the DWG-derived bucket and creates a result row.

This pipeline can consume an uploaded DXF or a prior accessible conversion result. Result selection is deterministic: latest successful Job first, then latest successful result row. It does not silently pick an arbitrary historic output.

## DXF to Excel

`DXF2EXCEL_PIPELINE_ENABLED=true` permits `task_type=extract_dxf_to_excel`. The service gathers accessible DXF files with the requested `batch_name`, stages readable objects, invokes the `dxf2excel` package, stores one workbook, and records partial download warnings in the Job steps.

Steps are `download_dxf_batch`, `run_dxf2excel_pipeline`, and `persist_excel_result`. A batch name is not an authorization scope: list, metadata, delete, and download operations must filter every file through the same SQL access boundary.

The current parent repository records `Stages/dxf2excel` only as gitlink commit `86e99dce5ebce992273c7df78ca13d58036f7472`, without `.gitmodules`, and that object is absent locally. The populated working directory makes this checkout work, but clean clones and image builds cannot rely on it. This must be repaired before the pipeline is considered reproducibly delivered.

## Excel Final

`EXCEL_FINAL_PIPELINE_ENABLED=true` enables dedicated upload/process endpoints and `task_type=process_excel_final`. Supported inputs are:

- Tekla tab/whitespace-delimited exports, sometimes carrying an `.xls` filename despite being text;
- real `.xlsx`/`.xlsm` workbooks with the required initial-table signature;
- legacy binary `.xls` workbooks that `xlrd` can parse and that contain the required business columns.

A generic spreadsheet with the right extension is a valid negative case. Detection tries text and workbook paths without treating a failed text decode as final proof of invalid input.

Steps are `download_excel_source`, `run_excel_final_pipeline`, `import_parts_to_db`, and `persist_excel_final_result`. The backend starts the standalone Stage as a child process with a bounded timeout and password passed through environment, not command-line arguments. Success stores the final workbook and imports one `excel_final_batches` row plus component and part rows. Failure removes transient batch rows only when the same attempt is still owned.

## Result and Download Resolution

Pipeline outputs are represented by a `files` row and an `analysis_results.result_file_id`. Result detail, download URL, and review checks delegate to the parent Job boundary. An unscoped Job is readable only by an administrator or its creator.

Single-file browser download obtains a 300-second signed path, then performs an authenticated fetch. On network error, 403, 408, 429, or 5xx it waits 500 ms and makes one second attempt with a new signature. ZIP endpoints stream a POST response and do not use the same re-sign loop.

## Cancellation, Retry, and Recovery

- Cancellation changes only an active matching Job; worker writes after cancellation are rejected by conditional updates.
- Retry is allowed from failed/cancelled state, increments attempt, resets terminal fields, and publishes `(job_id, new_attempt)`.
- Old one-argument messages map to attempt 1 and cannot claim attempt 2.
- Worker startup marks sufficiently stale running Jobs failed with `CELERY_WORKER_LOST`; an operator must verify dependencies before retrying.
- Celery result rows expire after 24 hours, but Job/JobStep business history remains in MySQL until an explicit retention policy is implemented.

## Enabling Checklist

1. Repair or verify Stage source ownership and locked dependencies.
2. Run Stage unit tests with representative valid and invalid samples.
3. Verify MySQL migrations, storage write/read/delete, and required handbook grants.
4. Start exactly one intended worker node for the queue and verify readiness.
5. Enable only the corresponding feature flag.
6. Submit through Nginx, observe Job steps/SSE, download the result, and compare SHA-256.
7. Exercise cancellation, retry, dependency outage, restart, and unauthorized access.

Do not enable `AGENT_ENABLED` or `CAD_WORKER_ENABLED` merely because their API/configuration symbols exist.
