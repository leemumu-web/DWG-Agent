import { apiClient, describeApiErrorAsync, type ApiEnvelope, type PageEnvelope } from '../../shared/api';
import type { Job } from '../jobs';
import type {
  WorkflowDetail,
  WorkflowRun,
  WorkflowStageExecutionPayload,
  WorkflowTemplate,
  DxfClassificationRun,
} from './workflow';

export interface WorkflowListParams {
  page?: number;
  page_size?: number;
  project_id?: number;
  status?: string;
}

export interface WorkflowCreatePayload {
  project_id: number;
  name: string;
  workflow_type: 'linux_production' | 'excel_delivery' | 'file_delivery';
  config?: Record<string, unknown>;
}

export async function listWorkflowTemplates() {
  const response = await apiClient.get<ApiEnvelope<WorkflowTemplate[]>>(
    '/api/v1/workflows/templates',
  );
  return response.data.data;
}

export async function listWorkflows(params: WorkflowListParams = {}) {
  const response = await apiClient.get<PageEnvelope<WorkflowRun>>('/api/v1/workflows', { params });
  return response.data;
}

export async function getWorkflow(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowDetail>>(`/api/v1/workflows/${workflowId}`);
  return response.data.data;
}

export async function downloadWorkflowArchive(workflowId: number) {
  try {
    const response = await apiClient.get<Blob>(
      `/api/v1/workflows/${workflowId}/download-archive`,
      { responseType: 'blob', timeout: 300_000 },
    );
    const disposition = response.headers['content-disposition'] as string | undefined;
    const encoded = disposition?.match(/filename\\*=UTF-8''([^;]+)/i)?.[1];
    const filename = encoded ? decodeURIComponent(encoded) : `workflow-${workflowId}.zip`;
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(() => {
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    }, 100);
  } catch (error) {
    throw new Error(await describeApiErrorAsync(error, '生产压缩包下载失败'));
  }
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
