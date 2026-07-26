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
    assert (
        "retry"
        not in source.split("export async function createConversionBatches", 1)[1].split(
            "export async function createDxf2ExcelJob", 1
        )[0]
    )


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


def test_browser_e2e_api_defaults_to_the_configured_same_origin_gateway():
    source = _e2e_source("support/test-env.ts")

    assert "process.env.PLAYWRIGHT_API_BASE_URL" in source
    assert "process.env.PLAYWRIGHT_FRONTEND_BASE_URL" in source
    assert source.index("PLAYWRIGHT_API_BASE_URL") < source.index(
        "PLAYWRIGHT_FRONTEND_BASE_URL"
    )


def test_every_reachable_bulk_drawing_transfer_has_visible_shared_progress():
    shared_api = _frontend_source("shared/api/transfer.ts")
    files_api = _frontend_source("features/files/files.api.ts")
    workflow_api = _frontend_source("features/workflows/workflows.api.ts")
    workflow_input_api = _frontend_source(
        "features/workflows/workflow-inputs.api.ts"
    )
    remnant_api = _frontend_source("features/remnant-inventory/api.ts")

    assert "onDownloadProgress" in shared_api
    assert "transferProgressFromAxios" in shared_api
    for contract in (
        "uploadFile(",
        "uploadZip(",
        "uploadFolder(",
        "downloadZip(",
        "downloadBatchZip(",
    ):
        assert contract in files_api
    assert "onTransferProgress" in files_api
    assert "onUploadProgress" in files_api
    assert "downloadBlob" in files_api
    assert "downloadArchive" in workflow_api
    assert "onProgress" in workflow_api
    assert "onUploadProgress" in workflow_input_api
    assert "onUploadProgress" in remnant_api

    visible_surfaces = {
        "features/files/FileUpload.tsx": "图纸批量上传",
        "features/files/ZipDownloadModal.tsx": "图纸打包下载",
        "features/cad-processing/components/conversion/ConversionUploadPanel.tsx": "图纸文件夹上传",
        "features/cad-processing/components/dxf2excel/DxfUploadPanel.tsx": "DXF 文件夹上传",
        "features/cad-processing/Dxf2ExcelPage.tsx": "批次下载",
        "features/workflows/ProductionInputPanel.tsx": "DWG 文件夹上传",
        "features/workflows/WorkflowStageArchiveCard.tsx": "阶段图纸结果下载",
        "features/workflows/WorkflowArtifactSummary.tsx": "全部生产产物下载",
        "features/workflows/DxfClassificationPanel.tsx": "分类图纸下载",
        "features/workflows/DrawingProcessingPanel.tsx": "拆板结果下载",
        "features/workflows/DrawingSelectiveExportControl.tsx": "分类图纸下载",
        "features/workflows/WorkflowBatchExportControl.tsx": "分批图纸下载",
        "features/workflows/WorkflowRetentionControl.tsx": "完整备份下载",
        "features/remnant-inventory/RemnantImportPanel.tsx": "余料图纸批量上传",
        "features/remnant-inventory/RemnantAutoImportPanel.tsx": "余料图纸文件夹上传",
    }
    for path, label in visible_surfaces.items():
        source = _frontend_source(path)
        assert "TransferProgressBar" in source, path
        assert label in source, path


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


def test_retired_system_health_console_has_no_orphaned_frontend_surface():
    page = _frontend_source("features/operations/pages/InfrastructurePage.tsx")

    assert "系统通信" not in page
    assert "RuntimeCommunicationPanel" not in page
    assert not (REPO_ROOT / "frontend/src/features/operations/api/system.ts").exists()


def test_frontend_has_global_recovery_and_connectivity_feedback():
    providers = _frontend_source("app/providers.tsx")
    boundary = _frontend_source("shared/components/AppErrorBoundary.tsx")
    connectivity = _frontend_source("shared/components/ConnectivityBanner.tsx")

    assert "AppErrorBoundary" in providers
    assert "retry: (failureCount, error)" in providers
    assert "online" in connectivity and "offline" in connectivity
    assert "重新加载当前页面" in boundary


def test_workflow_batch_cleanup_keeps_four_visible_categories_separate_from_split_pair():
    control = _frontend_source(
        "features/workflows/WorkflowBatchExportControl.tsx"
    )
    readme = _frontend_source("features/workflows/README.md")

    assert "选择要导出的四类数据" in control
    assert "提供四类生产文件下载" in readme
    assert "提供六类生产文件下载" not in readme


def test_task_console_uses_business_apis_instead_of_direct_database_crud():
    panel = _frontend_source(
        "features/operations/components/data-console/ProductionTaskPanel.tsx"
    )
    backend_constants = (
        REPO_ROOT / "backend/app/platform/config/constants.py"
    ).read_text(encoding="utf-8")
    backend_commands = (
        REPO_ROOT / "backend/app/modules/jobs/routes/commands.py"
    ).read_text(encoding="utf-8")

    for contract in ("listWorkflows", "listJobsPage", "cancelJob", "retryJob"):
        assert contract in panel
    for label in ("当前生产任务", "生产项目", "处理任务", "继续生产", "查看原因"):
        assert label in panel
    assert "createMySqlRow" not in panel
    assert "deleteMySqlRow" not in panel
    for task_type in (
        "convert_remnant_dwg",
        "parse_remnant_drawing",
        "convert_dwg_to_dxf",
        "convert_dxf_to_dwg",
        "extract_dxf_to_excel",
        "classify_steel_dxf",
        "split_steel_dxf",
        "process_excel_final",
    ):
        assert task_type in backend_constants
        assert task_type in panel
    for status in (
        "pending",
        "queued",
        "running",
        "waiting_cad_worker",
        "validating",
        "need_review",
        "succeeded",
        "failed",
        "cancelled",
    ):
        assert status in panel
    assert '"/{job_id}/cancellation-requests"' in backend_commands
    assert '"/{job_id}/retry-requests"' in backend_commands
    assert "DXF_SPLIT_WORKFLOW_EXECUTION_REQUIRED" in backend_commands
    assert "job.task_type !== 'split_steel_dxf'" in panel


def test_retired_daily_archive_console_has_no_orphaned_frontend_surface():
    infrastructure = _frontend_source("features/operations/pages/InfrastructurePage.tsx")

    assert "每日归档" not in infrastructure
    assert "DailyArchivePanel" not in infrastructure
    assert not (
        REPO_ROOT
        / "frontend/src/features/operations/components/DailyArchivePanel.tsx"
    ).exists()


def test_dashboard_is_a_real_linux_production_workbench():
    source = _frontend_source("features/dashboard/DashboardPage.tsx")

    assert "listWorkflows" in source
    assert "listWorkflowTemplates" in source
    assert "workflow_type: 'linux_production'" in source
    assert "生产工作台" in source
    assert "新建生产项目" in source
    assert "生产操作手册" in source
    assert "资料入库" in source
    assert "图纸分类" in source
    assert "整批拆板" in source
    assert "Excel 整理" in source
    assert "listFilesPage" not in source
    assert "listJobsPage" not in source
    assert "listPendingReviews" not in source


def test_reachable_frontend_has_no_development_stage_badge():
    sources = "\n".join(
        _frontend_source(path)
        for path in (
            "app/layout.tsx",
            "features/dashboard/DashboardPage.tsx",
            "features/identity/LoginPage.tsx",
        )
    )

    for retired_label in ("Stage 1", "本机开发版", "生产就绪骨架", "本机开发骨架"):
        assert retired_label not in sources


def test_data_console_presents_current_tables_and_storage_areas_in_chinese():
    presentation = _frontend_source(
        "features/operations/components/data-console/presentation.tsx"
    )
    page = _frontend_source("features/operations/pages/InfrastructurePage.tsx")
    tasks = _frontend_source(
        "features/operations/components/data-console/ProductionTaskPanel.tsx"
    )
    objects = _frontend_source(
        "features/operations/components/data-console/ObjectsPanel.tsx"
    )
    overview_query = (
        REPO_ROOT / "backend/app/modules/operations/data_catalog/queries.py"
    ).read_text(encoding="utf-8")

    for label in (
        "当前生产任务",
        "生产图纸转 DXF",
        "生产图纸分类",
        "生产图纸整批拆板",
        "生产 Excel 整理",
    ):
        assert label in tasks
    for bucket_label in (
        "原始 DWG",
        "转换后 DWG",
        "生产报告",
        "临时文件",
        "原始 DXF",
        "处理后 DXF",
    ):
        assert bucket_label in presentation
    assert "数据管理台" in page
    assert "生产任务" in page
    assert "文件存储" in page
    assert "DATA CONSOLE" not in page
    assert "MySQL 表结构" not in tasks
    assert ">NULL<" not in tasks
    assert ">PK<" not in tasks
    assert "MinIO 结构" not in objects
    assert 'title="Bucket"' not in objects
    assert ">Bucket<" not in objects
    assert "areas={overview.data?.storage.areas ?? []}" in page
    assert "dataSource={areas}" in objects
    assert "enabled: Boolean(bucket)" in objects
    assert "configured_areas" in overview_query
    assert '"purpose_codes"' in overview_query
    assert "'dwg-original'" not in presentation


def test_workflow_console_uses_backend_templates_files_and_stage_execution():
    api_source = _frontend_source("features/workflows/workflows.api.ts")
    list_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    artifact_source = _frontend_source(
        "features/workflows/WorkflowArtifactSummary.tsx"
    )
    future_source = _frontend_source("features/workflows/FutureStageNotice.tsx")
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
    assert "WorkflowArtifactSummary" in detail_source
    assert "FutureStageNotice" in detail_source
    assert "downloadWorkflowArchive" in artifact_source
    assert "已登记" in artifact_source
    assert "下载全部" in artifact_source
    assert "能力等待上线" in future_source
    assert "等待上线" in future_source
    assert "downloadFile" not in detail_source
    assert "executeWorkflowStage" in detail_source
    assert "当前不会提交虚假任务" not in detail_source
    assert "CAD 图纸业务算法和 Agent 不在本模块范围内" not in detail_source


def test_workflow_source_intake_has_guarded_dwg_excel_frontend_contract():
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    panel_source = _frontend_source("features/workflows/ProductionInputPanel.tsx")
    api_source = _frontend_source("features/workflows/workflow-inputs.api.ts")
    files_api_source = _frontend_source("features/files/files.api.ts")

    assert "<ProductionInputPanel" in detail_source
    assert "selectedStage.stage_code === 'source_intake'" in detail_source
    assert "sourceIntakeActive={selectedIsCurrent}" in detail_source
    assert "webkitdirectory" in panel_source
    assert "上传 Excel 文件" in panel_source
    assert "选择 DWG 文件夹" in panel_source
    assert 'accept=".xls,.xlsx"' in panel_source
    assert "确认，仅上传 DWG" in panel_source
    assert "MAX_FOLDER_FILES = 1000" in files_api_source
    assert "limitFolderUploadFiles(selected)" in panel_source
    assert "仅取前 ${MAX_FOLDER_FILES} 个文件上传" in panel_source
    assert "downloadFile" not in panel_source
    assert "冻结后不可修改" in panel_source
    assert "getWorkflow" in panel_source
    assert "workflow.current_stage !== 'source_intake'" in panel_source
    for path in (
        "/input-batch",
        "/input-excel",
        "/input-dwg-folder",
        "/input-folder",
        "/input-folder/restore",
        "/input-batch/conversion-requests",
        "/input-batch/freeze",
    ):
        assert path in api_source


def test_workflow_stage_mutations_recheck_authoritative_current_stage():
    classification_source = _frontend_source("features/workflows/DxfClassificationPanel.tsx")

    assert "getWorkflow" in classification_source
    assert "workflow.current_stage !== 'dxf_classification'" in classification_source


def test_production_project_drawer_uses_atomic_project_contract():
    page_source = _frontend_source("features/workflows/WorkflowsPage.tsx")
    drawer_source = _frontend_source("features/workflows/ProductionProjectCreateDrawer.tsx")
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
    success_handler = page_source.split("onSuccess: ({ workflow })", 1)[1].split("onError:", 1)[0]

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


def test_dxf_split_has_guarded_batch_console_without_inline_review_workbench():
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    panel_source = _frontend_source("features/workflows/DrawingProcessingPanel.tsx")
    export_source = _frontend_source(
        "features/workflows/WorkflowBatchExportControl.tsx"
    )
    selective_export_source = _frontend_source(
        "features/workflows/DrawingSelectiveExportControl.tsx"
    )
    export_actions_source = _frontend_source(
        "features/workflows/DrawingProcessingExportActions.tsx"
    )
    artifact_source = _frontend_source(
        "features/workflows/WorkflowArtifactSummary.tsx"
    )
    api_source = _frontend_source("features/workflows/workflows.api.ts")
    type_source = _frontend_source("features/workflows/workflow.ts")

    assert "<DrawingProcessingPanel" in detail_source
    assert "selectedStage.stage_code === 'drawing_processing'" in detail_source
    assert "['dxf_classification', 'drawing_processing'].includes" in detail_source
    assert "isCurrent={selectedIsCurrent}" in detail_source
    assert "开始整批拆板" in panel_source
    assert "drawing_processing" in panel_source
    assert "DrawingProcessingExportActions" in panel_source
    assert "WorkflowBatchExportControl" in export_actions_source
    assert "DrawingSelectiveExportControl" in export_actions_source
    assert 'title="03 · 图纸拆板与独立校验"' in panel_source
    assert "分批导出并清理" in export_source
    assert "原 DXF" not in export_source
    assert "category.label" in export_source
    assert "已保存，删除服务器文件" in export_source
    assert "此操作不可恢复" in export_source
    assert "WorkflowBatchExportControl" not in artifact_source
    assert "本批原图 ZIP（不含拆板成品）" in panel_source
    assert "需人工处理的图纸" not in panel_source
    assert "待确认" not in panel_source
    assert "已标记线下处理" not in panel_source
    assert "仅下载未通过原图 ZIP" not in panel_source
    assert "候选复核 ZIP" not in panel_source
    assert "重新整批拆板" not in panel_source
    assert "采用候选" not in panel_source
    assert "选择要导出的图纸" in selective_export_source
    assert "未通过的 BH" not in selective_export_source
    assert "category.label" in selective_export_source
    assert "下载后不会删除服务器文件" in selective_export_source
    assert "分类图纸导出" in selective_export_source
    assert "当前没有可导出的文件" in selective_export_source
    assert "重新下载" in selective_export_source
    assert "TransferProgressBar" in selective_export_source
    assert "ApiErrorAlert" in selective_export_source
    assert "ApiErrorAlert" in export_source
    assert "ApiErrorAlert" in panel_source
    assert "候选图" not in panel_source
    assert "getDxfSplitReviewItems" not in panel_source
    assert "decideDxfSplitReviewItem" not in panel_source
    assert "上传" not in panel_source
    assert "确认当前阶段" not in panel_source
    assert "getDxfSplitRun" in api_source
    assert "downloadWorkflowBatchExport" in api_source
    assert "downloadDrawingSelectiveExport" in api_source
    assert "onProgress" in api_source
    assert "/selective-export-preview" in api_source
    assert "/selective-exports" in api_source
    assert "/batch-exports/preview" in api_source
    assert "/purge" in api_source
    batch_download_source = api_source.split(
        "export async function downloadWorkflowBatchExport",
        1,
    )[1].split("export async function purgeWorkflowBatchExport", 1)[0]
    assert "downloadArchive" in batch_download_source
    assert "onProgress" in batch_download_source
    assert "source_size_bytes" in batch_download_source
    assert "document.createElement('a')" not in batch_download_source
    assert "downloadAllDxfClassificationArchive" in api_source
    assert "downloadDxfSplitManualReviewArchive" not in api_source
    assert "/drawing-processing/runs/${runId}/manual-review-archive" not in api_source
    assert "getDxfSplitReviewItems" not in api_source
    assert "decideDxfSplitReviewItem" not in api_source
    assert "DxfSplitRun" in type_source
    assert "DxfSplitReviewPage" not in type_source
    assert "automation_route: 'auto_accepted' | 'manual_review'" in type_source
    assert "split_report_file_id" not in type_source
    assert "validation_report_file" not in type_source


def test_workflow_primary_read_failures_share_operator_recovery_feedback():
    detail_source = _frontend_source("features/workflows/WorkflowDetailPage.tsx")
    classification_source = _frontend_source(
        "features/workflows/DxfClassificationPanel.tsx"
    )
    input_source = _frontend_source("features/workflows/ProductionInputPanel.tsx")
    shared_source = _frontend_source("shared/components/ApiErrorAlert.tsx")
    error_source = _frontend_source("shared/api/error.ts")

    assert "ApiErrorAlert" in detail_source
    assert "ApiErrorAlert" in classification_source
    assert "ApiErrorAlert" in input_source
    assert "处理建议：" in shared_source
    assert "apiErrorRecovery" in shared_source
    assert "服务器暂时无法完成操作" in error_source
    assert "先刷新当前状态" in error_source
    assert "WORKFLOW_STAGE_INPUT_INCOMPLETE" in error_source
    assert "返回前序阶段补齐必需产物" in error_source


def test_operator_errors_are_chinese_and_hide_backend_diagnostics():
    error_source = _frontend_source("shared/api/error.ts")
    boundary_source = _frontend_source("shared/components/AppErrorBoundary.tsx")
    task_source = _frontend_source(
        "features/operations/components/data-console/ProductionTaskPanel.tsx"
    )
    input_source = _frontend_source("features/workflows/ProductionInputPanel.tsx")
    stage_sources = [
        _frontend_source("features/workflows/WorkflowDetailPage.tsx"),
        _frontend_source("features/workflows/DxfClassificationPanel.tsx"),
        _frontend_source("features/workflows/DrawingProcessingPanel.tsx"),
        _frontend_source("features/workflows/WorkflowRetentionControl.tsx"),
        _frontend_source("features/excel-processing/ExcelFinalPage.tsx"),
    ]

    assert "TECHNICAL_MESSAGE" in error_source
    assert "safeOperatorText" in error_source
    assert "operatorErrorMessage" in error_source
    assert "请求编号" in error_source
    assert "codePart" not in error_source
    assert "console.error" not in boundary_source
    assert "Request ID" not in boundary_source
    assert "错误编号" not in task_source
    assert "其他处理任务（${taskType}）" not in task_source
    assert "${issue.file_name ? `${issue.file_name} · ` : ''}${issue.code}" not in input_source
    for source in stage_sources:
        assert "operatorErrorMessage" in source


def test_dashboard_contains_complete_operator_manual():
    dashboard = _frontend_source("features/dashboard/DashboardPage.tsx")

    assert "生产操作手册" in dashboard
    for section in (
        "1. 建立项目与准备资料",
        "2. 资料上传与入库冻结",
        "3. 图纸分类与数量核对",
        "4. BH、BOX 整批拆板",
        "5. Excel 整理与重量核验",
        "6. 下载交付与异常处理",
    ):
        assert section in dashboard
    for rule in (
        "只处理第一张",
        "前 1000 个",
        "原始 DWG 只负责留档",
        "原长版和余量增长版",
        "板材统一按 7.85",
        "BH、BOX、BT 拆板后的腹板与翼板重量要合并",
        "只会解锁下一阶段",
    ):
        assert rule in dashboard


def test_drawing_progress_download_recovers_when_transfer_fails():
    panel = _frontend_source("features/workflows/DrawingProcessingPanel.tsx")

    assert "const [launchFailed, setLaunchFailed]" in panel
    assert "if (launchFailed) return false" in panel
    assert "const launch = async (next: WorkflowBatchExport)" in panel
    assert "setProgress" in panel
    assert "TransferProgressBar" in panel
    assert "setLaunchFailed(true)" in panel
    assert "!launchFailed && ACTIVE_EXPORT_STATUSES.has" in panel


def test_data_console_has_two_focused_task_and_storage_workspaces():
    page_source = _frontend_source("features/operations/pages/InfrastructurePage.tsx")
    api_source = _frontend_source("features/operations/api/dataAdmin.ts")
    type_source = _frontend_source("features/operations/types/dataAdmin.ts")
    objects_panel = _frontend_source(
        "features/operations/components/data-console/ObjectsPanel.tsx"
    )
    task_panel = _frontend_source(
        "features/operations/components/data-console/ProductionTaskPanel.tsx"
    )

    assert "useSearchParams" in page_source
    for key in ("tasks", "storage"):
        assert f"key: '{key}'" in page_source
    for path in (
        "/api/v1/data-admin/overview",
        "/api/v1/data-admin/objects/tree",
        "/api/v1/data-admin/objects/moves",
    ):
        assert path in api_source
    for contract in (
        "DataAdminOverview",
        "DataAdminFile",
        "StorageObject",
    ):
        assert f"interface {contract}" in type_source
    assert "getStorageObjectTree" in objects_panel
    assert "uploadDataAdminObject" in objects_panel
    assert "moveDataAdminObject" in objects_panel
    assert "deleteDataAdminObject" in objects_panel
    assert "listWorkflows" in task_panel
    assert "listJobsPage" in task_panel
    assert "mysql/tables" not in api_source
    assert "remediations" not in api_source
    assert not (
        REPO_ROOT
        / "frontend/src/features/operations/components/data-console/MySqlWorkspace.tsx"
    ).exists()
    assert "destroyOnHidden" in page_source


def test_data_console_is_authenticated_and_audit_log_remains_admin_only():
    router_source = _frontend_source("app/router.tsx")
    layout_source = _frontend_source("app/layout.tsx")

    assert 'path="/data-console"' in router_source
    assert '<Navigate to="/data-console" replace />' in router_source
    assert "roles: ['admin']" in layout_source
    assert "key: '/data-console'" in layout_source
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
