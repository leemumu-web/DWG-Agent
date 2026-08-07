import {
  apiClient,
  downloadBlob,
  type ApiEnvelope,
  type PageEnvelope,
  type TransferProgressHandler,
} from '../../shared/api';
import type { Job } from '../jobs';
import type { Project } from '../projects';
import type {
  WorkflowDetail,
  WorkflowRun,
  WorkflowStageExecutionPayload,
  WorkflowTemplate,
  DxfClassificationGroupPage,
  DxfClassificationRun,
  DxfSplitRun,
  DrawingSelectiveExport,
  DrawingSelectiveExportCategory,
  DrawingSelectiveExportPreview,
  WorkflowBatchExport,
  WorkflowBatchExportPreview,
  WorkflowBatchExportPurgeResult,
  WorkflowExportCategory,
  WorkflowExcelStagePreflight,
  WorkflowExcelStage2Preflight,
  WorkflowRetentionExport,
  WorkflowRetentionPreview,
} from './workflow';

export interface WorkflowListParams {
  page?: number;
  page_size?: number;
  project_id?: number;
  workflow_type?: 'linux_production' | 'excel_delivery' | 'file_delivery';
  status?: string;
}

export interface WorkflowCreatePayload {
  project_id: number;
  name: string;
  workflow_type: 'linux_production' | 'excel_delivery' | 'file_delivery';
  config?: Record<string, unknown>;
}

export interface ProductionProjectCreatePayload {
  code: string;
  name: string;
  description?: string;
}

export interface ProductionProjectCreateResult {
  project: Project;
  workflow: WorkflowDetail;
}

export interface WorkflowSummary {
  total: number;
  running: number;
  waiting: number;
  completed: number;
}

export interface WorkflowPage extends PageEnvelope<WorkflowRun> {
  summary: WorkflowSummary;
}

export async function listWorkflowTemplates() {
  const response = await apiClient.get<ApiEnvelope<WorkflowTemplate[]>>(
    '/api/v1/workflows/templates',
  );
  return response.data.data;
}

export async function listWorkflows(params: WorkflowListParams = {}) {
  const response = await apiClient.get<WorkflowPage>('/api/v1/workflows', { params });
  return response.data;
}

export async function createProductionProject(
  payload: ProductionProjectCreatePayload,
) {
  const response = await apiClient.post<ApiEnvelope<ProductionProjectCreateResult>>(
    '/api/v1/workflows/production-projects',
    payload,
  );
  return response.data.data;
}

export async function getWorkflow(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowDetail>>(`/api/v1/workflows/${workflowId}`);
  return response.data.data;
}

async function downloadArchive(
  url: string,
  fallbackName: string,
  errorMessage: string,
  onProgress?: TransferProgressHandler,
  expectedBytes?: number,
  signal?: AbortSignal,
) {
  return downloadBlob({
    url,
    fallbackName,
    errorMessage,
    onProgress,
    expectedBytes,
    signal,
  });
}

export async function downloadWorkflowArchive(
  workflowId: number,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/download-archive`,
    `workflow-${workflowId}.zip`,
    '生产压缩包下载失败',
    onProgress,
    undefined,
    signal,
  );
}

export async function downloadWorkflowStageArchive(
  workflowId: number,
  stageCode: string,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/stages/${stageCode}/download-archive`,
    `workflow-${workflowId}-${stageCode}.zip`,
    '阶段结果压缩包下载失败',
    onProgress,
    undefined,
    signal,
  );
}

export async function downloadWorkflowExcelStageResult(
  workflowId: number,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/stages/excel_stage1/download-result`,
    `workflow-${workflowId}-excel-stage1.xlsx`,
    'Excel 结果下载失败',
    onProgress,
    undefined,
    signal,
  );
}

export async function downloadWorkflowExcelStage2Result(
  workflowId: number,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/stages/excel_stage2/download-result`,
    `workflow-${workflowId}-excel-stage2.xlsx`,
    'Excel 第二阶段结果下载失败',
    onProgress,
    undefined,
    signal,
  );
}

export async function downloadWorkflowExcelStage2ReaderResult(
  workflowId: number,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/stages/excel_stage2/download-reader-result`,
    `workflow-${workflowId}-bh-setback.xlsx`,
    'BH 左右进读取表下载失败',
    onProgress,
    undefined,
    signal,
  );
}

export async function getWorkflowExcelStagePreflight(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowExcelStagePreflight>>(
    `/api/v1/workflows/${workflowId}/stages/excel_stage1/preflight`,
  );
  return response.data.data;
}

export async function getWorkflowExcelStage2Preflight(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowExcelStage2Preflight>>(
    `/api/v1/workflows/${workflowId}/stages/excel_stage2/preflight`,
  );
  return response.data.data;
}

export async function createWorkflow(payload: WorkflowCreatePayload) {
  const response = await apiClient.post<ApiEnvelope<WorkflowDetail>>('/api/v1/workflows', payload);
  return response.data.data;
}

export async function startWorkflow(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowDetail>>(`/api/v1/workflows/${workflowId}/start`);
  return response.data.data;
}

export async function completeWorkflowStage(workflowId: number, stageCode: string) {
  const response = await apiClient.post<ApiEnvelope<WorkflowDetail>>(
    `/api/v1/workflows/${workflowId}/stages/${stageCode}/completion`,
  );
  return response.data.data;
}

export async function cancelWorkflow(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowDetail>>(
    `/api/v1/workflows/${workflowId}/cancellation-requests`,
  );
  return response.data.data;
}

export async function executeWorkflowStage(
  workflowId: number,
  stageCode: string,
  payload: WorkflowStageExecutionPayload,
) {
  const response = await apiClient.post<
    ApiEnvelope<{ workflow: WorkflowDetail; job: Job; reused: boolean; retried: boolean }>
  >(`/api/v1/workflows/${workflowId}/stages/${stageCode}/executions`, payload);
  return response.data.data;
}

export async function getDxfClassification(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<DxfClassificationRun | null>>(
    `/api/v1/workflows/${workflowId}/dxf-classification`,
  );
  return response.data.data;
}

export async function getDxfClassificationGroup(
  workflowId: number,
  groupKey: string,
  page = 1,
  pageSize = 20,
) {
  const response = await apiClient.get<ApiEnvelope<DxfClassificationGroupPage>>(
    `/api/v1/workflows/${workflowId}/dxf-classification/groups/${encodeURIComponent(groupKey)}`,
    { params: { page, page_size: pageSize } },
  );
  return response.data.data;
}

export async function downloadDxfClassificationGroupArchive(
  workflowId: number,
  groupKey: string,
  onProgress?: TransferProgressHandler,
  expectedBytes?: number,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/groups/${encodeURIComponent(groupKey)}/download-archive`,
    `workflow-${workflowId}-dxf-group.zip`,
    '分类文件夹下载失败',
    onProgress,
    expectedBytes,
    signal,
  );
}

export async function downloadAllDxfClassificationArchive(
  workflowId: number,
  onProgress?: TransferProgressHandler,
  expectedBytes?: number,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/download-archive`,
    `workflow-${workflowId}-all-classified-dxf.zip`,
    '全部 DXF 下载失败',
    onProgress,
    expectedBytes,
    signal,
  );
}

export async function downloadDxfClassificationFile(
  workflowId: number,
  groupKey: string,
  outputName: string,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/groups/${encodeURIComponent(groupKey)}/files/${encodeURIComponent(outputName)}/download`,
    outputName,
    '分类 DXF 下载失败',
    onProgress,
    undefined,
    signal,
  );
}

export async function getDxfSplitRun(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<DxfSplitRun | null>>(
    `/api/v1/workflows/${workflowId}/drawing-processing`,
  );
  return response.data.data;
}

export async function getDrawingSelectiveExportPreview(
  workflowId: number,
  runId: number,
) {
  const response = await apiClient.get<ApiEnvelope<DrawingSelectiveExportPreview>>(
    `/api/v1/workflows/${workflowId}/drawing-processing/runs/${runId}/selective-export-preview`,
  );
  return response.data.data;
}

export async function createDrawingSelectiveExport(
  workflowId: number,
  runId: number,
  categories: DrawingSelectiveExportCategory[],
) {
  const response = await apiClient.post<ApiEnvelope<DrawingSelectiveExport>>(
    `/api/v1/workflows/${workflowId}/drawing-processing/runs/${runId}/selective-exports`,
    { categories },
  );
  return response.data.data;
}

export async function downloadDrawingSelectiveExport(
  prepared: DrawingSelectiveExport,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  if (!prepared.download_url) {
    throw new Error('本次选择导出没有可用的下载地址');
  }
  return downloadArchive(
    prepared.download_url,
    prepared.filename,
    '选择导出下载失败',
    onProgress,
    prepared.source_size_bytes,
    signal,
  );
}
export async function getWorkflowBatchExportPreview(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowBatchExportPreview>>(
    `/api/v1/workflows/${workflowId}/batch-exports/preview`,
  );
  return response.data.data;
}

export async function createWorkflowBatchExport(
  workflowId: number,
  categories: WorkflowExportCategory[],
) {
  const response = await apiClient.post<ApiEnvelope<WorkflowBatchExport>>(
    `/api/v1/workflows/${workflowId}/batch-exports`,
    { categories },
  );
  return response.data.data;
}

export async function getWorkflowBatchExport(
  workflowId: number,
  exportUid: string,
) {
  const response = await apiClient.get<ApiEnvelope<WorkflowBatchExport>>(
    `/api/v1/workflows/${workflowId}/batch-exports/${encodeURIComponent(exportUid)}`,
  );
  return response.data.data;
}

export async function downloadWorkflowBatchExport(
  exportRow: WorkflowBatchExport,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  if (!exportRow.download_url) {
    throw new Error('本次分批导出没有可用的下载地址');
  }
  return downloadArchive(
    exportRow.download_url,
    exportRow.filename,
    '分批导出下载失败',
    onProgress,
    exportRow.source_size_bytes,
    signal,
  );
}

export async function purgeWorkflowBatchExport(
  workflowId: number,
  exportUid: string,
) {
  const response = await apiClient.post<ApiEnvelope<WorkflowBatchExportPurgeResult>>(
    `/api/v1/workflows/${workflowId}/batch-exports/${encodeURIComponent(exportUid)}/purge`,
  );
  return response.data.data;
}

export async function getWorkflowRetentionPreview(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowRetentionPreview>>(
    `/api/v1/workflows/${workflowId}/retention-preview`,
  );
  return response.data.data;
}

export async function createWorkflowRetentionExport(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowRetentionExport>>(
    `/api/v1/workflows/${workflowId}/retention-exports`,
  );
  return response.data.data;
}

export async function getLatestWorkflowRetentionExport(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowRetentionExport | null>>(
    `/api/v1/workflows/${workflowId}/retention-exports/latest`,
  );
  return response.data.data;
}

export async function getWorkflowRetentionExport(
  workflowId: number,
  exportUid: string,
) {
  const response = await apiClient.get<ApiEnvelope<WorkflowRetentionExport>>(
    `/api/v1/workflows/${workflowId}/retention-exports/${encodeURIComponent(exportUid)}`,
  );
  return response.data.data;
}

export async function downloadWorkflowRetentionExport(
  exportRow: WorkflowRetentionExport,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
) {
  if (!exportRow.download_url) {
    throw new Error('本次完整备份没有可用的下载地址，请重新生成备份凭据');
  }
  return downloadArchive(
    exportRow.download_url,
    exportRow.filename,
    '完整备份下载失败',
    onProgress,
    exportRow.source_size_bytes,
    signal,
  );
}

export async function queueWorkflowRetentionPurge(
  workflowId: number,
  exportUid: string,
  confirmation: string,
) {
  const response = await apiClient.post<ApiEnvelope<WorkflowRetentionExport>>(
    `/api/v1/workflows/${workflowId}/retention-exports/${encodeURIComponent(exportUid)}/purge`,
    { confirmation },
  );
  return response.data.data;
}
