import { apiClient, type ApiEnvelope } from '../../shared/api';
import type {
  WorkflowInputBatch,
  WorkflowInputConversion,
} from './workflow-input';

export async function createWorkflowInputBatch(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-batch`,
  );
  return response.data.data;
}

export async function getWorkflowInputBatch(workflowId: number) {
  const response = await apiClient.get<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-batch`,
  );
  return response.data.data;
}

export async function registerWorkflowInputFile(workflowId: number, fileId: number) {
  const response = await apiClient.post<
    ApiEnvelope<{ batch: WorkflowInputBatch; item_id: number; reused: boolean }>
  >(`/api/v1/workflows/${workflowId}/input-batch/files`, { file_id: fileId });
  return response.data.data;
}

export async function removeWorkflowInputFile(workflowId: number, itemId: number) {
  await apiClient.delete(`/api/v1/workflows/${workflowId}/input-batch/files/${itemId}`);
}

export async function requestWorkflowInputConversions(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowInputConversion>>(
    `/api/v1/workflows/${workflowId}/input-batch/conversion-requests`,
  );
  return response.data.data;
}

export async function freezeWorkflowInputBatch(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-batch/freeze`,
  );
  return response.data.data;
}
