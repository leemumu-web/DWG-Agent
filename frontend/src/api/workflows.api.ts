import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type { Job } from '../types/job';
import type {
  WorkflowArtifact,
  WorkflowArtifactCreatePayload,
  WorkflowDetail,
  WorkflowRun,
  WorkflowStageExecutionPayload,
  WorkflowTemplate,
} from '../types/workflow';

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

export async function createWorkflowArtifact(
  workflowId: number,
  payload: WorkflowArtifactCreatePayload,
) {
  const response = await apiClient.post<
    ApiEnvelope<{ artifact: WorkflowArtifact; workflow: WorkflowDetail; reused: boolean }>
  >(`/api/v1/workflows/${workflowId}/artifacts`, payload);
  return response.data.data;
}

export async function executeWorkflowStage(
  workflowId: number,
  stageCode: string,
  payload: WorkflowStageExecutionPayload,
) {
  const response = await apiClient.post<
    ApiEnvelope<{ workflow: WorkflowDetail; job: Job; reused: boolean }>
  >(`/api/v1/workflows/${workflowId}/stages/${stageCode}/executions`, payload);
  return response.data.data;
}
