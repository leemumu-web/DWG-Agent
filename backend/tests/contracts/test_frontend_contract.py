from __future__ import annotations

from tests.support.paths import REPO_ROOT


def _frontend_source(path: str) -> str:
    return (REPO_ROOT / "frontend/src" / path).read_text(encoding="utf-8")


def _e2e_source(path: str) -> str:
    return (REPO_ROOT / "frontend/tests/e2e" / path).read_text(encoding="utf-8")


def test_frontend_password_change_matches_backend_patch_contract():
    source = _frontend_source("shared/auth/api.ts")

    assert "apiClient.patch" in source
    assert "'/api/v1/auth/password'" in source


def test_frontend_keeps_access_token_in_session_storage_only():
    source = _frontend_source("shared/auth/store.ts")

    assert "sessionStorage" in source
    assert "localStorage" not in source


def test_frontend_sse_never_puts_access_token_in_url():
    source = _frontend_source("features/jobs/useJobEvents.ts")

    assert "?token=" not in source
    assert "encodeURIComponent(token)" not in source
    assert "new EventSource(url, { withCredentials: true })" in source


def test_auth_init_can_restore_session_from_httponly_cookie():
    source = _frontend_source("shared/auth/useAuthInit.ts")

    assert "setSession(data.access_token, data.user)" in source
    assert "if (!token)" not in source


def test_password_change_immediately_clears_revoked_frontend_session():
    source = _frontend_source("features/identity/ProfilePage.tsx")

    assert "clearSession()" in source
    assert "navigate('/login'" in source


def test_non_idempotent_uploads_are_not_automatically_retried():
    source = _frontend_source("features/files/files.api.ts")

    assert "apiClient.post<ApiEnvelope<StoredFile>>('/api/v1/files'" in source
    assert "apiClient.post<ApiEnvelope<ZipUploadResult>>('/api/v1/files/upload-zip'" in source
    assert "fetchWithTimeout" not in source


def test_folder_upload_concurrency_fits_default_api_database_pool():
    api_source = _frontend_source("features/files/files.api.ts")
    upload_panel = _frontend_source(
        "features/cad-processing/components/conversion/ConversionUploadPanel.tsx"
    )

    # Default API pool budget is DB_POOL_SIZE=2 + MAX_OVERFLOW=2. The browser
    # must not open eight simultaneous upload transactions against four slots.
    assert "opts?.concurrency ?? 4" in api_source
    assert "concurrency: 4" in upload_panel
    assert "concurrency: 8" not in upload_panel


def test_generated_api_source_documents_zip_preview_and_conflicts():
    source = (REPO_ROOT / "scripts/docs/generate_api.py").read_text(encoding="utf-8")

    assert "/api/v1/files/download-zip/preview" in source
    assert "missing_count" in source
    assert "FILE_EXPORT_FORMAT_UNAVAILABLE" in source
    assert "STORAGE_INCONSISTENT" in source


def test_conversion_submission_preserves_partial_chunk_results():
    source = _frontend_source("features/jobs/jobs.api.ts")

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
    api_source = _frontend_source("features/files/files.api.ts")
    page_source = _frontend_source("features/cad-processing/ConversionPage.tsx")

    assert "interface BatchBulkDeleteResult" in api_source
    assert "'/api/v1/files/batches/bulk-delete'" in api_source
    assert "bulkDeleteBatches(selectedBatchNames)" in page_source
    handler = page_source.split("const handleBatchDelete", 1)[1].split(
        "// ── batch zip file IDs", 1
    )[0]
    assert "bulkDeleteFiles" not in handler
    assert "listFiles" not in handler


def test_download_retries_with_a_fresh_signed_url_through_auth_interceptor():
    source = _frontend_source("features/files/files.api.ts")

    assert "isRetryableDownloadError" in source
    assert "apiClient.get<Blob>(url" in source
    assert "for (let attempt = 0; attempt < 2; attempt++)" in source
    loop = source.split("for (let attempt = 0; attempt < 2; attempt++)", 1)[1]
    assert loop.index("getFileDownloadUrl(fileId)") < loop.index("apiClient.get<Blob>(url")


def test_browser_e2e_uses_session_storage_and_cookie_sse_auth():
    sources = "\n".join(
        _e2e_source(path)
        for path in (
            "files/files-page-buttons.spec.ts",
            "contracts/api-contract.spec.ts",
        )
    )

    assert "localStorage" not in sources
    assert "sessionStorage" in sources
    assert "?token=" not in sources


def test_excel_final_has_frontend_api_types_route_and_tab():
    api_source = _frontend_source("features/excel-processing/api.ts")
    router_source = _frontend_source("app/router.tsx")
    tabs_source = _frontend_source("features/files/FilesLayout.tsx")
    page_source = _frontend_source("features/excel-processing/ExcelFinalPage.tsx")
    type_source = _frontend_source("features/excel-processing/types.ts")

    assert "/api/v1/excel-final/upload-and-process" in api_source
    assert "/api/v1/excel-final/batches" in api_source
    assert "/api/v1/excel-final/parts/search" in api_source
    assert 'path="excel-final"' in router_source
    assert "/files/excel-final" in tabs_source
    assert "uploadAndProcessExcel" in page_source
    assert "ExcelFinalBatch" in type_source


def test_excel_final_retry_refreshes_status_and_replaced_batch_cache():
    page_source = _frontend_source("features/excel-processing/ExcelFinalPage.tsx")
    drawer_source = _frontend_source(
        "features/excel-processing/components/ExcelFinalBatchDrawer.tsx"
    )

    assert "queryKey: ['excel-final-status', jobId]" in page_source
    assert "refetchType: 'all'" in page_source
    assert "updateUrl({ batch_id: null })" in page_source
    assert "parseExcelFinalUrlState(searchParams)" in page_source
    assert "some((job) => ACTIVE_STATUSES.has(job.status))" in page_source
    assert "? 3000" in page_source
    assert "<ExcelFinalBatchDrawer" in page_source
    assert "<Drawer" in drawer_source
    assert 'size="min(1180px, 96vw)"' in drawer_source


def test_dxf_to_excel_result_bridges_to_excel_final_without_dynamic_imports():
    page_source = _frontend_source("features/cad-processing/Dxf2ExcelPage.tsx")
    api_source = _frontend_source("features/excel-processing/api.ts")

    assert "processExcelFinalFile" in page_source
    assert "from '../excel-processing'" in page_source
    assert "finalSubmissionRef.current.has(batchName)" in page_source
    assert "getJobResults(extractionJob.id)" in page_source
    assert "`dxf2excel-${extractionJob.id}-${excel.result_file_id}`" in page_source
    assert "processExcelFinalFile(excel.result_file_id, requestKey)" in page_source
    assert "`/files/excel-final?job_id=${finalJob.job_id}`" in page_source
    assert "import(" not in page_source
    assert "'/api/v1/excel-final/process'" in api_source
    assert "'Idempotency-Key': requestKey" in api_source


def test_frontend_system_health_lists_every_pipeline_flag():
    source = _frontend_source("features/operations/api/system.ts")

    for feature in (
        "dxf_pipeline",
        "dxf2dwg_pipeline",
        "dxf2excel_pipeline",
        "excel_final_pipeline",
    ):
        assert feature in source


def test_frontend_has_global_recovery_and_connectivity_feedback():
    providers = _frontend_source("app/providers.tsx")
    boundary = _frontend_source("shared/components/AppErrorBoundary.tsx")
    connectivity = _frontend_source("shared/components/ConnectivityBanner.tsx")

    assert "AppErrorBoundary" in providers
    assert "retry: (failureCount, error)" in providers
    assert "online" in connectivity and "offline" in connectivity
    assert "重新加载当前页面" in boundary


def test_runtime_console_consumes_maintenance_and_real_storage_contracts():
    api_source = _frontend_source("features/operations/api/controlPlane.ts")
    runtime_panel = _frontend_source(
        "features/operations/components/data-console/RuntimeCommunicationPanel.tsx"
    )
    overview_panel = _frontend_source(
        "features/operations/components/data-console/OverviewPanel.tsx"
    )

    assert "/maintenance/reconcile-stale-jobs" in api_source
    assert "恢复超时运行任务" in runtime_panel
    assert "getInfrastructureOverview" in overview_panel
    assert "MinIO" in overview_panel


def test_daily_archive_console_uses_preview_queue_poll_and_signed_download_contracts():
    panel = _frontend_source("features/operations/components/DailyArchivePanel.tsx")
    data_api = _frontend_source("features/operations/api/dataAdmin.ts")
    files_api = _frontend_source("features/files/files.api.ts")
    infrastructure = _frontend_source("features/operations/pages/InfrastructurePage.tsx")

    assert "/daily-archives/preview" in data_api
    assert "/daily-archives/${archiveId}" in data_api
    assert "preview_token" in data_api
    assert "idempotency_key" in data_api
    assert "每日归档" in infrastructure
    assert "非破坏式每日整理" in panel
    assert "refetchInterval" in panel
    assert "downloadFile" in panel
    assert "/download-url" in files_api


def test_dashboard_turns_existing_task_and_review_state_into_next_actions():
    source = _frontend_source("features/dashboard/DashboardPage.tsx")

    assert "今日工作建议" in source
    assert "failed > 0" in source
    assert "reviewsQ.data" in source
    assert "navigate(action.to)" in source
    assert 'aria-label={`查看任务 ${j.id} 详情`}' in source


def test_workflow_console_uses_backend_templates_files_and_stage_execution():
    api_source = _frontend_source("features/workflows/workflows.api.ts")
    list_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    type_source = _frontend_source("features/workflows/workflow.ts")

    for path in ("/api/v1/workflows/templates", "/executions"):
        assert path in api_source
    for contract in (
        "WorkflowTemplate",
        "WorkflowStageCapability",
        "WorkflowStageExecutionPayload",
    ):
        assert f"interface {contract}" in type_source
    assert "linux_production" in api_source
    assert "listWorkflows" in list_source
    assert "createProductionProject" in list_source
    assert "getWorkflow" in detail_source
    assert "downloadWorkflowArchive" in detail_source
    assert "downloadFile" not in detail_source
    assert "executeWorkflowStage" in detail_source
    assert "当前不会提交虚假任务" in detail_source
    assert "CAD 图纸业务算法和 Agent 不在本模块范围内" not in detail_source


def test_workflow_source_intake_has_guarded_dwg_excel_frontend_contract():
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    panel_source = _frontend_source("features/workflows/ProductionInputPanel.tsx")
    api_source = _frontend_source("features/workflows/workflow-inputs.api.ts")

    assert "<ProductionInputPanel" in detail_source
    assert "selectedStage.stage_code === 'source_intake'" in detail_source
    assert "sourceIntakeActive={selectedIsCurrent}" in detail_source
    assert "webkitdirectory" in panel_source
    assert "上传 Excel 文件" in panel_source
    assert "选择 DWG 文件夹" in panel_source
    assert 'accept=".xls,.xlsx"' in panel_source
    assert "确认，仅上传 DWG" in panel_source
    assert "downloadFile" not in panel_source
    assert "冻结后不可修改" in panel_source
    for path in (
        "/input-batch",
        "/input-excel",
        "/input-dwg-folder",
        "/input-folder",
        "/input-batch/conversion-requests",
        "/input-batch/freeze",
    ):
        assert path in api_source


def test_production_project_drawer_uses_atomic_project_contract():
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    drawer_source = _frontend_source(
        "features/workflows/ProductionProjectCreateDrawer.tsx"
    )
    api_source = _frontend_source("features/workflows/workflows.api.ts")

    assert "新建生产项目" in page_source
    assert "<ProductionProjectCreateDrawer" in page_source
    assert "项目编号" in drawer_source
    assert "项目名称" in drawer_source
    assert "项目说明" in drawer_source
    assert "创建项目并进入工作流" in drawer_source
    assert "project_id" not in drawer_source
    assert "批次名称" not in drawer_source
    assert "/production-projects" in api_source
    assert "createProductionProject" in api_source


def test_production_submission_navigates_to_one_dedicated_detail_workspace():
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    rail_source = _frontend_source("features/workflows/WorkflowStageRail.tsx")
    success_handler = page_source.split("onSuccess: ({ workflow })", 1)[1].split(
        "onError:", 1
    )[0]

    assert "setCreateOpen(false)" in success_handler
    assert "navigate(`/workflows/${workflow.id}`)" in success_handler
    assert "<ProductionInputPanel" not in page_source
    assert "<ProductionInputPanel" in detail_source
    assert "<WorkflowStageRail" in detail_source
    assert "workflow-stage-rail" in rail_source
    assert 'type="button"' in rail_source


def test_dxf_classification_has_dedicated_guarded_frontend_console():
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    panel_source = _frontend_source("features/workflows/DxfClassificationPanel.tsx")
    api_source = _frontend_source("features/workflows/workflows.api.ts")
    type_source = _frontend_source("features/workflows/workflow.ts")

    assert "<DxfClassificationPanel" in detail_source
    assert "selectedStage.stage_code === 'dxf_classification'" in detail_source
    assert "isCurrent={selectedIsCurrent}" in detail_source
    assert "开始 DXF 分类分流" in panel_source
    assert "steel_dxf_classification" in panel_source
    assert "workflow-classification-folders" in panel_source
    assert "下载全部 DXF" in panel_source
    assert "自动发现" in panel_source
    assert "张图纸需要处理" in panel_source
    assert "分类报告已纳入生产压缩包" not in panel_source
    assert "分类清单已纳入生产压缩包" not in panel_source
    assert "downloadFile" not in panel_source
    assert "getDxfClassification" in api_source
    assert "getDxfClassificationGroup" in api_source
    assert "downloadDxfClassificationGroupArchive" in api_source
    assert "downloadAllDxfClassificationArchive" in api_source
    assert "/dxf-classification/groups/" in api_source
    assert "/dxf-classification/download-archive" in api_source
    assert "DxfClassificationRun" in type_source
    assert "DxfClassificationGroup" in type_source


def test_data_console_has_five_url_controlled_tabs_and_api_contracts():
    page_source = _frontend_source("features/operations/pages/InfrastructurePage.tsx")
    api_source = _frontend_source("features/operations/api/dataAdmin.ts")
    type_source = _frontend_source("features/operations/types/dataAdmin.ts")
    files_panel = _frontend_source(
        "features/operations/components/data-console/FilesPanel.tsx"
    )
    transfers_panel = _frontend_source(
        "features/operations/components/data-console/TransfersPanel.tsx"
    )
    consistency_panel = _frontend_source(
        "features/operations/components/data-console/ConsistencyPanel.tsx"
    )

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
    assert "listStorageScans" in consistency_panel
    assert "getDataAdminFile" in files_panel
    assert "getFileTransfer" in transfers_panel
    assert "处置预检" in consistency_panel
    assert "RemediationDrawer" in consistency_panel
    assert "登记详情" in files_panel
    assert "流水详情" in transfers_panel
    assert "destroyOnHidden" in page_source


def test_data_console_and_audit_log_routes_are_admin_only():
    router_source = _frontend_source("app/router.tsx")
    layout_source = _frontend_source("app/layout.tsx")

    assert "<RequireRoles allowed={['admin']} />" in router_source
    assert "roles: ['admin']" in layout_source
    assert "viewer" not in router_source
    assert "viewer" not in layout_source


def test_operational_tables_use_bounded_server_pagination():
    files_api = _frontend_source("features/files/files.api.ts")
    jobs_api = _frontend_source("features/jobs/jobs.api.ts")
    audit_api = _frontend_source("features/operations/api/auditLogs.ts")
    conversion_page = _frontend_source("features/cad-processing/ConversionPage.tsx")
    audit_page = _frontend_source("features/operations/pages/AuditLogsPage.tsx")

    assert "listFilesPage" in files_api
    assert "listJobsPage" in jobs_api
    assert "listAuditLogsPage" in audit_api
    assert "listFilesPage" in conversion_page
    assert "listJobsForFiles" in conversion_page
    assert "latest_per_file: true" in jobs_api
    assert "current: page" in conversion_page
    assert "listAuditLogsPage" in audit_page
    assert "total: logsQ.data?.pagination.total" in audit_page
