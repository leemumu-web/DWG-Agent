import {
  apiClient,
  completedTransferProgress,
  initialTransferProgress,
  transferProgressFromAxios,
  type ApiEnvelope,
  type TransferProgressHandler,
} from '../../shared/api';
import type {
  WorkflowInputBatch,
  WorkflowInputConversion,
} from './workflow-input';

// A full 5000-drawing folder can take substantially longer than a normal form
// submission while the API records each source file. This agrees with the
// dedicated Nginx route and does not change the 512 MiB request-size boundary.
const WORKFLOW_INPUT_DWG_FOLDER_TIMEOUT_MS = 1_800_000;
const WORKFLOW_INPUT_BATCH_TIMEOUT_MS = 120_000;
const WORKFLOW_INPUT_FREEZE_TIMEOUT_MS = 300_000;

export async function createWorkflowInputBatch(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-batch`,
    undefined,
    { timeout: WORKFLOW_INPUT_BATCH_TIMEOUT_MS },
  );
  return response.data.data;
}

export async function getWorkflowInputBatch(
  workflowId: number,
  itemPage = 1,
  itemPageSize = 50,
) {
  const response = await apiClient.get<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-batch`,
    {
      params: { item_page: itemPage, item_page_size: itemPageSize },
      timeout: WORKFLOW_INPUT_BATCH_TIMEOUT_MS,
    },
  );
  return response.data.data;
}

export async function uploadWorkflowInputExcel(workflowId: number, file: File) {
  const form = new FormData();
  form.append('upload', file, file.name);
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-excel`,
    form,
    { timeout: 300_000 },
  );
  return response.data.data;
}

export async function uploadWorkflowInputDwgFolder(
  workflowId: number,
  files: File[],
  onProgress?: TransferProgressHandler,
) {
  const form = new FormData();
  const relativePaths = files.map((file) => file.webkitRelativePath);
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  files.forEach((file) => form.append('uploads', file, file.name));
  form.append('relative_paths', JSON.stringify(relativePaths));
  onProgress?.(initialTransferProgress(totalBytes));
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-dwg-folder`,
    form,
    {
      timeout: WORKFLOW_INPUT_DWG_FOLDER_TIMEOUT_MS,
      onUploadProgress: (event) => onProgress?.(
        transferProgressFromAxios(event, totalBytes),
      ),
    },
  );
  onProgress?.(completedTransferProgress(totalBytes, totalBytes));
  return response.data.data;
}

export async function clearWorkflowInputFolder(workflowId: number) {
  await apiClient.delete(`/api/v1/workflows/${workflowId}/input-folder`);
}

export async function restoreWorkflowInputFolder(workflowId: number) {
  const response = await apiClient.post<ApiEnvelope<WorkflowInputBatch>>(
    `/api/v1/workflows/${workflowId}/input-folder/restore`,
  );
  return response.data.data;
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
    undefined,
    { timeout: WORKFLOW_INPUT_FREEZE_TIMEOUT_MS },
  );
  return response.data.data;
}
