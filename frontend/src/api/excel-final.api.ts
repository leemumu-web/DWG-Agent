import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type {
  ExcelFinalBatch,
  ExcelFinalBatchSummary,
  ExcelFinalPart,
  ExcelFinalProcessStatus,
  ExcelFinalSubmission,
} from '../types/excel-final';

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
