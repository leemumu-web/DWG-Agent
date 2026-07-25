import { apiClient, describeApiErrorAsync, type ApiEnvelope, type PageEnvelope } from '../../shared/api';
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
) {
  try {
    const response = await apiClient.get<Blob>(
      url,
      { responseType: 'blob', timeout: 300_000 },
    );
    const disposition = (
      typeof response.headers.get === 'function'
        ? response.headers.get('content-disposition')
        : response.headers['content-disposition']
    ) as string | undefined;
    const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encoded ? decodeURIComponent(encoded) : fallbackName;
    const objectUrl = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(() => {
      document.body.removeChild(anchor);
      URL.revokeObjectURL(objectUrl);
    }, 100);
  } catch (error) {
    throw new Error(await describeApiErrorAsync(error, errorMessage));
  }
}

export async function downloadWorkflowArchive(workflowId: number) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/download-archive`,
    `workflow-${workflowId}.zip`,
    '生产压缩包下载失败',
  );
}

export async function downloadWorkflowStageArchive(
  workflowId: number,
  stageCode: string,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/stages/${stageCode}/download-archive`,
    `workflow-${workflowId}-${stageCode}.zip`,
    '阶段结果压缩包下载失败',
  );
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
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/groups/${encodeURIComponent(groupKey)}/download-archive`,
    `workflow-${workflowId}-dxf-group.zip`,
    '分类文件夹下载失败',
  );
}

export async function downloadAllDxfClassificationArchive(workflowId: number) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/dxf-classification/download-archive`,
    `workflow-${workflowId}-all-classified-dxf.zip`,
    '全部 DXF 下载失败',
  );
}

export async function getDxfSplitRun(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<DxfSplitRun | null>>(
    `/api/v1/workflows/${workflowId}/drawing-processing`,
  );
  return response.data.data;
}

export async function downloadDxfSplitManualReviewArchive(
  workflowId: number,
  runId: number,
) {
  return downloadArchive(
    `/api/v1/workflows/${workflowId}/drawing-processing/runs/${runId}/manual-review-archive`,
    `workflow-${workflowId}-split-run-${runId}-manual-review.zip`,
    '未通过原图压缩包下载失败',
  );
}
