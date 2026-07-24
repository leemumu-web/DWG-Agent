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

export async function uploadWorkflowInputFolder(workflowId: number, files: File[]) {
  const form = new FormData();
  const relativePaths = files.map((file) => file.webkitRelativePath);
  files.forEach((file) => form.append('uploads', file, file.name));
  form.append('relative_paths', JSON.stringify(relativePaths));
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-folder`,
    form,
    { timeout: 300_000 },
  );
  return response.data.data;
}

export async function clearWorkflowInputFolder(workflowId: number) {
  await apiClient.delete(`/api/v1/workflows/${workflowId}/input-folder`);
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
