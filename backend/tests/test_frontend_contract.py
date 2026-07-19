from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _frontend_source(path: str) -> str:
    return (REPO_ROOT / "frontend/src" / path).read_text(encoding="utf-8")


def _e2e_source(path: str) -> str:
    return (REPO_ROOT / "frontend/tests/e2e" / path).read_text(encoding="utf-8")


def test_frontend_password_change_matches_backend_patch_contract():
    source = _frontend_source("api/auth.api.ts")

    assert "apiClient.patch" in source
    assert "'/api/v1/auth/password'" in source


def test_frontend_keeps_access_token_in_session_storage_only():
    source = _frontend_source("stores/auth.store.ts")

    assert "sessionStorage" in source
    assert "localStorage" not in source


def test_frontend_sse_never_puts_access_token_in_url():
    source = _frontend_source("hooks/useJobEvents.ts")

    assert "?token=" not in source
    assert "encodeURIComponent(token)" not in source
    assert "new EventSource(url, { withCredentials: true })" in source


def test_auth_init_can_restore_session_from_httponly_cookie():
    source = _frontend_source("hooks/useAuthInit.ts")

    assert "setSession(data.access_token, data.user)" in source
    assert "if (!token)" not in source


def test_password_change_immediately_clears_revoked_frontend_session():
    source = _frontend_source("features/profile/ProfilePage.tsx")

    assert "clearSession()" in source
    assert "navigate('/login'" in source


def test_non_idempotent_uploads_are_not_automatically_retried():
    source = _frontend_source("api/files.api.ts")

    assert "apiClient.post<ApiEnvelope<StoredFile>>('/api/v1/files'" in source
    assert "apiClient.post<ApiEnvelope<ZipUploadResult>>('/api/v1/files/upload-zip'" in source
    assert "fetchWithTimeout" not in source


def test_folder_upload_concurrency_fits_default_api_database_pool():
    api_source = _frontend_source("api/files.api.ts")
    page_source = _frontend_source("components/ConversionPage.tsx")

    # Default API pool budget is DB_POOL_SIZE=2 + MAX_OVERFLOW=2. The browser
    # must not open eight simultaneous upload transactions against four slots.
    assert "opts?.concurrency ?? 4" in api_source
    assert "concurrency: 4" in page_source
    assert "concurrency: 8" not in page_source


def test_generated_api_source_documents_zip_preview_and_conflicts():
    source = (REPO_ROOT / "scripts/generate_api_docs.py").read_text(encoding="utf-8")

    assert "/api/v1/files/download-zip/preview" in source
    assert "missing_count" in source
    assert "FILE_EXPORT_FORMAT_UNAVAILABLE" in source
    assert "STORAGE_INCONSISTENT" in source


def test_conversion_submission_preserves_partial_chunk_results():
    source = _frontend_source("api/jobs.api.ts")

    assert "interface ConversionBatchSubmission" in source
    assert "submittedJobs" in source
    assert "submittedFileIds" in source
    assert "unsubmittedFileIds" in source
    assert "errors" in source
    assert "Promise.allSettled" in source
    assert "createConversionBatches" in source
    assert "retry" not in source.split("export async function createConversionBatches", 1)[1].split(
        "export async function createDxf2ExcelJob", 1
    )[0]


def test_folder_bulk_delete_uses_atomic_batch_endpoint():
    api_source = _frontend_source("api/files.api.ts")
    page_source = _frontend_source("components/ConversionPage.tsx")

    assert "interface BatchBulkDeleteResult" in api_source
    assert "'/api/v1/files/batches/bulk-delete'" in api_source
    assert "bulkDeleteBatches(selectedBatchNames)" in page_source
    handler = page_source.split("const handleBatchDelete", 1)[1].split(
        "// ── batch zip file IDs", 1
    )[0]
    assert "bulkDeleteFiles" not in handler
    assert "listFiles" not in handler


def test_download_retries_with_a_fresh_signed_url_through_auth_interceptor():
    source = _frontend_source("api/files.api.ts")

    assert "isRetryableDownloadError" in source
    assert "apiClient.get<Blob>(url" in source
    assert "for (let attempt = 0; attempt < 2; attempt++)" in source
    loop = source.split("for (let attempt = 0; attempt < 2; attempt++)", 1)[1]
    assert loop.index("getFileDownloadUrl(fileId)") < loop.index("apiClient.get<Blob>(url")


def test_browser_e2e_uses_session_storage_and_cookie_sse_auth():
    sources = "\n".join(
        _e2e_source(path)
        for path in (
            "files-page-buttons.spec.ts",
            "jobs-page-buttons.spec.ts",
            "api-contract.spec.ts",
        )
    )

    assert "localStorage" not in sources
    assert "sessionStorage" in sources
    assert "?token=" not in sources


def test_excel_final_has_frontend_api_types_route_and_tab():
    api_source = _frontend_source("api/excel-final.api.ts")
    router_source = _frontend_source("app/router.tsx")
    tabs_source = _frontend_source("features/files/FilesLayout.tsx")
    page_source = _frontend_source("features/files/ExcelFinalPage.tsx")
    type_source = _frontend_source("types/excel-final.ts")

    assert "/api/v1/excel-final/upload-and-process" in api_source
    assert "/api/v1/excel-final/batches" in api_source
    assert "/api/v1/excel-final/parts/search" in api_source
    assert 'path="excel-final"' in router_source
    assert "/files/excel-final" in tabs_source
    assert "uploadAndProcessExcel" in page_source
    assert "ExcelFinalBatch" in type_source


def test_excel_final_retry_refreshes_status_and_replaced_batch_cache():
    page_source = _frontend_source("features/files/ExcelFinalPage.tsx")
    drawer_source = _frontend_source(
        "features/files/excel-final/ExcelFinalBatchDrawer.tsx"
    )

    assert "queryKey: ['excel-final-status', jobId]" in page_source
    assert "refetchType: 'all'" in page_source
    assert "updateUrl({ batch_id: null })" in page_source
    assert "parseExcelFinalUrlState(searchParams)" in page_source
    assert "some((job) => ACTIVE_STATUSES.has(job.status)) ? 3000 : false" in page_source
    assert "<ExcelFinalBatchDrawer" in page_source
    assert "<Drawer" in drawer_source
    assert 'size="min(1180px, 96vw)"' in drawer_source


def test_dxf_to_excel_result_bridges_to_excel_final_without_dynamic_imports():
    page_source = _frontend_source("features/files/Dxf2ExcelPage.tsx")
    api_source = _frontend_source("api/excel-final.api.ts")

    assert "import { processExcelFinalFile }" in page_source
    assert "finalSubmissionRef.current.has(batchName)" in page_source
    assert "getJobResults(extractionJob.id)" in page_source
    assert "`dxf2excel-${extractionJob.id}-${excel.result_file_id}`" in page_source
    assert "processExcelFinalFile(excel.result_file_id, requestKey)" in page_source
    assert "`/files/excel-final?job_id=${finalJob.job_id}`" in page_source
    assert "import(" not in page_source
    assert "'/api/v1/excel-final/process'" in api_source
    assert "'Idempotency-Key': requestKey" in api_source


def test_job_drawer_loads_steps_for_the_current_attempt_only():
    source = _frontend_source("features/jobs/JobsPage.tsx")

    assert "getJobSteps(jobId, job.attempt)" in source
    assert "getJobSteps(retried.id, retried.attempt)" in source


def test_frontend_system_health_lists_every_pipeline_flag():
    source = _frontend_source("api/system.api.ts")

    for feature in (
        "dxf_pipeline",
        "dxf2dwg_pipeline",
        "dxf2excel_pipeline",
        "excel_final_pipeline",
    ):
        assert feature in source


def test_workflow_console_uses_backend_templates_files_and_stage_execution():
    api_source = _frontend_source("api/workflows.api.ts")
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    type_source = _frontend_source("types/workflow.ts")

    for path in (
        "/api/v1/workflows/templates",
        "/artifacts",
        "/executions",
    ):
        assert path in api_source
    for contract in (
        "WorkflowTemplate",
        "WorkflowStageCapability",
        "WorkflowArtifactCreatePayload",
        "WorkflowStageExecutionPayload",
    ):
        assert f"interface {contract}" in type_source
    assert "linux_production" in api_source
    assert "listFilesPage" in page_source
    assert "listBatches" in page_source
    assert "downloadFile" in page_source
    assert "createWorkflowArtifact" in page_source
    assert "executeWorkflowStage" in page_source
    assert "接口已预留" in page_source
    assert "CAD 图纸业务算法和 Agent 不在本模块范围内" not in page_source


def test_workflow_source_intake_has_guarded_dwg_excel_frontend_contract():
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    panel_source = _frontend_source("features/workflows/ProductionInputPanel.tsx")
    api_source = _frontend_source("api/workflow-inputs.api.ts")

    assert "<ProductionInputPanel" in page_source
    assert "actionableStage.stage_code === 'source_intake'" in page_source
    assert 'accept=".dwg"' in panel_source
    assert 'accept=".xls,.xlsx"' in panel_source
    assert "上传 DWG" in panel_source
    assert "上传 Excel" in panel_source
    assert "INPUT_DXF_NOT_ALLOWED" in panel_source
    assert "冻结后不可修改" in panel_source
    assert "crypto.randomUUID()" in panel_source
    for path in (
        "/input-batch",
        "/input-batch/files",
        "/input-batch/conversion-requests",
        "/input-batch/freeze",
    ):
        assert path in api_source


def test_production_submission_entry_creates_starts_and_opens_upload():
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")

    assert "提交生产批次" in page_source
    assert "创建并进入上传" in page_source
    assert "await createWorkflow" in page_source
    assert "await startWorkflow(created.id)" in page_source
    assert page_source.index("await createWorkflow") < page_source.index(
        "await startWorkflow(created.id)"
    )
    assert "启动并进入上传" in page_source
    assert "workflow_type: 'linux_production'" in page_source
    assert "先创建项目" in page_source


def test_production_submission_stays_in_one_drawer_until_files_are_uploaded():
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    success_handler = page_source.split("onSuccess: ({ workflow, startError })", 1)[1].split(
        "onError:", 1
    )[0]

    assert "setSubmissionWorkflow(workflow)" in success_handler
    assert "setCreateOpen(false)" not in success_handler
    assert "submissionWorkflow" in page_source
    assert "重试启动并进入上传" in page_source
    create_drawer = page_source.split("title={submissionWorkflow", 1)[1]
    assert "<ProductionInputPanel" in create_drawer


def test_data_console_has_five_url_controlled_tabs_and_api_contracts():
    page_source = _frontend_source("features/admin/InfrastructurePage.tsx")
    api_source = _frontend_source("api/data-admin.api.ts")
    type_source = _frontend_source("types/data-admin.ts")

    assert "useSearchParams" in page_source
    for key in ("overview", "files", "objects", "transfers", "consistency"):
        assert f"key: '{key}'" in page_source
    for path in (
        "/api/v1/data-admin/overview",
        "/api/v1/data-admin/files",
        "/api/v1/data-admin/objects",
        "/api/v1/data-admin/transfers",
        "/api/v1/data-admin/scans",
        "/api/v1/data-admin/remediations/preview",
        "/api/v1/data-admin/remediations/execute",
    ):
        assert path in api_source
    for contract in (
        "DataAdminOverview",
        "DataAdminFile",
        "StorageObject",
        "FileTransfer",
        "StorageScanRun",
        "StorageScanFinding",
    ):
        assert f"interface {contract}" in type_source
    assert "listStorageScans" in page_source
    assert "getDataAdminFile" in page_source
    assert "getFileTransfer" in page_source
    assert "处置预检" in page_source
    assert "RemediationDrawer" in page_source
    assert "登记详情" in page_source
    assert "流水详情" in page_source
    assert "destroyOnHidden" in page_source


def test_auditor_can_open_read_only_data_console():
    router_source = _frontend_source("app/router.tsx")
    layout_source = _frontend_source("app/layout.tsx")

    assert "<RequireRoles allowed={['admin', 'auditor']} />" in router_source
    assert "roles: ['admin', 'auditor']" in layout_source


def test_operational_tables_use_bounded_server_pagination():
    files_api = _frontend_source("api/files.api.ts")
    jobs_api = _frontend_source("api/jobs.api.ts")
    audit_api = _frontend_source("api/audit-logs.api.ts")
    conversion_page = _frontend_source("components/ConversionPage.tsx")
    jobs_page = _frontend_source("features/jobs/JobsPage.tsx")
    audit_page = _frontend_source("features/admin/AuditLogsPage.tsx")

    assert "listFilesPage" in files_api
    assert "listJobsPage" in jobs_api
    assert "listAuditLogsPage" in audit_api
    assert "listFilesPage" in conversion_page
    assert "listJobsForFiles" in conversion_page
    assert "latest_per_file: true" in jobs_api
    assert "current: page" in conversion_page
    assert "listJobsPage" in jobs_page
    assert "total: query.data?.pagination.total" in jobs_page
    assert "listAuditLogsPage" in audit_page
    assert "total: logsQ.data?.pagination.total" in audit_page
