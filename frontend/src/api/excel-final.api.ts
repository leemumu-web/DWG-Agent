import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type {
  ExcelFinalBatch,
  ExcelFinalBatchSummary,
  ExcelFinalComponent,
  ExcelFinalHealth,
  ExcelFinalOverview,
  ExcelFinalPart,
  ExcelFinalProcessStatus,
  ExcelFinalSubmission,
  ExcelFinalWeightLookup,
} from '../types/excel-final';

export async function getExcelFinalHealth() {
  const response = await apiClient.get<ApiEnvelope<ExcelFinalHealth>>(
    '/api/v1/excel-final/health',
  );
  return response.data.data;
}

export async function getExcelFinalOverview() {
  const response = await apiClient.get<ApiEnvelope<ExcelFinalOverview>>(
    '/api/v1/excel-final/overview',
  );
  return response.data.data;
}

export async function uploadAndProcessExcel(file: File) {
  const form = new FormData();
  form.append('upload', file);
  const response = await apiClient.post<ApiEnvelope<ExcelFinalSubmission>>(
    '/api/v1/excel-final/upload-and-process',
    form,
    { timeout: 300_000 },
  );
  return response.data.data;
}

export async function getExcelFinalProcessStatus(jobId: number) {
  const response = await apiClient.get<ApiEnvelope<ExcelFinalProcessStatus>>(
    `/api/v1/excel-final/process/${jobId}`,
  );
  return response.data.data;
}

export async function listExcelFinalBatches(page = 1, pageSize = 50) {
  const response = await apiClient.get<PageEnvelope<ExcelFinalBatchSummary>>(
    '/api/v1/excel-final/batches',
    { params: { page, page_size: pageSize } },
  );
  return response.data;
}

export async function getExcelFinalBatch(batchId: number) {
  const response = await apiClient.get<ApiEnvelope<ExcelFinalBatch>>(
    `/api/v1/excel-final/batches/${batchId}`,
  );
  return response.data.data;
}

export interface ExcelFinalPartFilters {
  spec?: string;
  material?: string;
  part_no?: string;
  part_type?: string;
}

export async function listExcelFinalParts(
  batchId: number,
  page: number,
  pageSize: number,
  filters: ExcelFinalPartFilters,
) {
  const response = await apiClient.get<PageEnvelope<ExcelFinalPart>>(
    `/api/v1/excel-final/batches/${batchId}/parts`,
    { params: { page, page_size: pageSize, ...filters } },
  );
  return response.data;
}

export async function getExcelFinalPart(batchId: number, partId: number) {
  const response = await apiClient.get<ApiEnvelope<ExcelFinalPart>>(
    `/api/v1/excel-final/batches/${batchId}/parts/${partId}`,
  );
  return response.data.data;
}

export async function listExcelFinalComponents(
  batchId: number,
  page = 1,
  pageSize = 20,
) {
  const response = await apiClient.get<PageEnvelope<ExcelFinalComponent>>(
    `/api/v1/excel-final/batches/${batchId}/components`,
    { params: { page, page_size: pageSize } },
  );
  return response.data;
}

export async function searchExcelFinalParts(
  filters: Pick<ExcelFinalPartFilters, 'spec' | 'material' | 'part_no'>,
  page = 1,
  pageSize = 50,
) {
  const response = await apiClient.get<PageEnvelope<ExcelFinalPart>>(
    '/api/v1/excel-final/parts/search',
    { params: { page, page_size: pageSize, ...filters } },
  );
  return response.data;
}

export async function lookupExcelFinalWeight(spec: string) {
  const response = await apiClient.get<ApiEnvelope<ExcelFinalWeightLookup>>(
    '/api/v1/excel-final/weights/lookup',
    { params: { spec } },
  );
  return response.data.data;
}

export async function processExcelFinalFile(fileId: number) {
  const response = await apiClient.post<ApiEnvelope<ExcelFinalSubmission>>(
    '/api/v1/excel-final/process',
    undefined,
    { params: { file_id: fileId } },
  );
  return response.data.data;
}
