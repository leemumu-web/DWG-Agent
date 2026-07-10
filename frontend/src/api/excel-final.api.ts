import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type {
  ExcelFinalBatch,
  ExcelFinalPart,
  ExcelFinalComponent,
  ExcelFinalHealth,
  ExcelFinalStatus,
  BatchDetail,
  WeightLookupResult,
  UploadResult,
  ProcessResult,
  UploadAndProcessResult,
} from '../types/excel-final';

// ── Health ──────────────────────────────────────────────────────────────

export async function checkHealth(): Promise<ExcelFinalHealth> {
  const res = await apiClient.get<ApiEnvelope<ExcelFinalHealth>>(
    '/api/v1/excel-final/health',
  );
  return res.data.data;
}

// ── Upload & Process ──────────────────────────────────────────────────

export async function uploadExcel(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('upload', file);
  const res = await apiClient.post<ApiEnvelope<UploadResult>>(
    '/api/v1/excel-final/upload',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120_000 },
  );
  return res.data.data;
}

export async function processFile(fileId: number): Promise<ProcessResult> {
  const res = await apiClient.post<ApiEnvelope<ProcessResult>>(
    `/api/v1/excel-final/process`,
    undefined,
    { params: { file_id: fileId } },
  );
  return res.data.data;
}

export async function uploadAndProcess(file: File): Promise<UploadAndProcessResult> {
  const form = new FormData();
  form.append('upload', file);
  const res = await apiClient.post<ApiEnvelope<UploadAndProcessResult>>(
    '/api/v1/excel-final/upload-and-process',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120_000 },
  );
  return res.data.data;
}

export async function getProcessStatus(jobId: number): Promise<ExcelFinalStatus> {
  const res = await apiClient.get<ApiEnvelope<ExcelFinalStatus>>(
    `/api/v1/excel-final/process/${jobId}`,
  );
  return res.data.data;
}

export async function downloadProcessResult(jobId: number): Promise<{ url: string; expires_in: number }> {
  const res = await apiClient.get<ApiEnvelope<{ url: string; expires_in: number }>>(
    `/api/v1/excel-final/process/${jobId}/download`,
  );
  return res.data.data;
}

// ── Batch queries ──────────────────────────────────────────────────────

export async function listBatches(page = 1, pageSize = 20): Promise<PageEnvelope<ExcelFinalBatch>> {
  const res = await apiClient.get<PageEnvelope<ExcelFinalBatch>>(
    '/api/v1/excel-final/batches',
    { params: { page, page_size: pageSize } },
  );
  return res.data;
}

export async function getBatchDetail(batchId: number): Promise<BatchDetail> {
  const res = await apiClient.get<ApiEnvelope<BatchDetail>>(
    `/api/v1/excel-final/batches/${batchId}`,
  );
  return res.data.data;
}

export async function listBatchParts(
  batchId: number,
  filters?: { spec?: string; material?: string; part_no?: string; part_type?: string },
  page = 1,
  pageSize = 50,
): Promise<PageEnvelope<ExcelFinalPart>> {
  const params: Record<string, unknown> = { page, page_size: pageSize };
  if (filters?.spec) params.spec = filters.spec;
  if (filters?.material) params.material = filters.material;
  if (filters?.part_no) params.part_no = filters.part_no;
  if (filters?.part_type) params.part_type = filters.part_type;
  const res = await apiClient.get<PageEnvelope<ExcelFinalPart>>(
    `/api/v1/excel-final/batches/${batchId}/parts`,
    { params },
  );
  return res.data;
}

export async function listBatchComponents(batchId: number): Promise<ExcelFinalComponent[]> {
  const res = await apiClient.get<ApiEnvelope<ExcelFinalComponent[]>>(
    `/api/v1/excel-final/batches/${batchId}/components`,
  );
  return res.data.data;
}

export async function getPartDetail(batchId: number, partId: number): Promise<ExcelFinalPart> {
  const res = await apiClient.get<ApiEnvelope<ExcelFinalPart>>(
    `/api/v1/excel-final/batches/${batchId}/parts/${partId}`,
  );
  return res.data.data;
}

// ── Search ─────────────────────────────────────────────────────────────

export async function searchParts(
  filters: { spec?: string; material?: string; part_no?: string },
  page = 1,
  pageSize = 50,
): Promise<PageEnvelope<ExcelFinalPart>> {
  const params: Record<string, unknown> = { page, page_size: pageSize, ...filters };
  const res = await apiClient.get<PageEnvelope<ExcelFinalPart>>(
    '/api/v1/excel-final/parts/search',
    { params },
  );
  return res.data;
}

// ── Tools ──────────────────────────────────────────────────────────────

export async function lookupWeight(spec: string): Promise<WeightLookupResult> {
  const res = await apiClient.get<ApiEnvelope<WeightLookupResult>>(
    '/api/v1/excel-final/weights/lookup',
    { params: { spec } },
  );
  return res.data.data;
}
