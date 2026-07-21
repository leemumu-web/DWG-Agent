import { apiClient, fetchAllPages, type ApiEnvelope, type PageEnvelope } from '../../shared/api';
import type { Job, JobStep } from './job';
import type { AnalysisResult } from './result';

/** Re-export so existing `import type { JobResult }` keeps working. The
 *  canonical type lives in types/result.ts (AnalysisResult) — job results
 *  and pending reviews are the same backend AnalysisResultRead shape. */
export type JobResult = AnalysisResult;

/** Fetch ALL jobs across pages, optionally filtered by task_type.
 *  The FilesPage in-memory files↔jobs join via params.file_id needs every
 *  job for the given task type to be present. Uses page_size=200
 *  (the backend hard maximum) and aggregates pages until total is reached. */
export async function listJobs(taskType?: string) {
  const params: Record<string, unknown> = {};
  if (taskType) params.task_type = taskType;
  return fetchAllPages<Job>('/api/v1/jobs', params);
}

const MAX_BULK_IDS = 200;

function chunksOf<T>(values: T[], size = MAX_BULK_IDS): T[][] {
  const chunks: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    chunks.push(values.slice(index, index + size));
  }
  return chunks;
}

/** Fetch all matching jobs while respecting the backend's 200-file filter limit. */
export async function listJobsForFiles(taskType: string, fileIds: number[]): Promise<Job[]> {
  if (fileIds.length === 0) return [];
  const pages = await Promise.all(
    chunksOf(fileIds).map((chunk) => listJobsPage({
      page: 1,
      page_size: MAX_BULK_IDS,
      task_type: taskType,
      file_ids: chunk.join(','),
      latest_per_file: true,
    })),
  );
  return pages.flatMap((page) => page.data);
}

export interface JobListParams {
  page: number;
  page_size: number;
  task_type?: string;
  status?: string;
  search?: string;
  file_ids?: string;
  latest_per_file?: boolean;
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
}

export async function listJobsPage(params: JobListParams) {
  const res = await apiClient.get<PageEnvelope<Job>>('/api/v1/jobs', { params });
  return res.data;
}

export async function getJob(jobId: number) {
  const res = await apiClient.get<ApiEnvelope<Job>>(`/api/v1/jobs/${jobId}`);
  return res.data.data;
}

export async function getJobSteps(jobId: number, attempt?: number) {
  return fetchAllPages<JobStep>(`/api/v1/jobs/${jobId}/steps`, {
    ...(attempt === undefined ? {} : { attempt }),
  });
}

export async function getJobResults(jobId: number) {
  const res = await apiClient.get<PageEnvelope<JobResult>>(`/api/v1/jobs/${jobId}/results`, {
    params: { page_size: 200 },
  });
  return res.data.data;
}

export async function retryJob(jobId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>(`/api/v1/jobs/${jobId}/retry-requests`);
  return res.data.data;
}

export async function createDxfJob(fileId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'convert_dwg_to_dxf',
    precision_level: 'normal',
    params: { file_id: fileId },
  });
  return res.data.data;
}

export async function createDxf2DwgJob(fileId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'convert_dxf_to_dwg',
    precision_level: 'normal',
    params: { file_id: fileId },
  });
  return res.data.data;
}

/** Submit one ODA directory-batch task per group instead of one task per file. */
export interface ConversionBatchSubmission {
  submittedJobs: Job[];
  submittedFileIds: number[];
  unsubmittedFileIds: number[];
  errors: string[];
}

export async function createConversionBatches(
  taskType: string,
  fileIds: number[],
): Promise<ConversionBatchSubmission> {
  const uniqueFileIds = Array.from(new Set(fileIds));
  if (uniqueFileIds.length === 0) {
    return {
      submittedJobs: [],
      submittedFileIds: [],
      unsubmittedFileIds: [],
      errors: [],
    };
  }

  const pendingChunks = chunksOf(uniqueFileIds);
  const submittedJobs: Job[] = [];
  const submittedFileIds: number[] = [];
  const unsubmittedFileIds: number[] = [];
  const errors: string[] = [];

  for (let index = 0; index < pendingChunks.length; index += 3) {
    const wave = pendingChunks.slice(index, index + 3);
    const settled = await Promise.allSettled(
      wave.map((chunk) =>
        apiClient.post<ApiEnvelope<{ jobs: Job[] }>>('/api/v1/jobs/batches', {
          task_type: taskType,
          file_ids: chunk,
          precision_level: 'normal',
        })),
    );
    settled.forEach((result, resultIndex) => {
      const chunk = wave[resultIndex];
      if (result.status === 'fulfilled') {
        submittedJobs.push(...result.value.data.data.jobs);
        submittedFileIds.push(...chunk);
        return;
      }
      unsubmittedFileIds.push(...chunk);
      errors.push(
        result.reason instanceof Error && result.reason.message
          ? result.reason.message
          : '批量提交失败',
      );
    });
  }

  return { submittedJobs, submittedFileIds, unsubmittedFileIds, errors };
}

export async function createDxf2ExcelJob(batchName: string) {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'extract_dxf_to_excel',
    precision_level: 'normal',
    params: { batch_name: batchName },
  });
  return res.data.data;
}

export async function createFrameworkSmokeJob() {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'framework_smoke_test',
    precision_level: 'normal',
    params: { source: 'frontend' },
  });
  return res.data.data;
}

export async function cancelJob(jobId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>(`/api/v1/jobs/${jobId}/cancellation-requests`);
  return res.data.data;
}

export async function cancelAllJobs(): Promise<{ cancelled_count: number }> {
  const res = await apiClient.post<ApiEnvelope<{ cancelled_count: number }>>(
    '/api/v1/jobs/cancel-all-active',
  );
  return res.data.data;
}

/** Cancel only the jobs visible in the caller's conversion scope. */
export async function cancelJobs(jobIds: number[]): Promise<{ cancelled_count: number }> {
  if (jobIds.length === 0) return { cancelled_count: 0 };
  const responses = await Promise.all(
    chunksOf(Array.from(new Set(jobIds))).map((chunk) =>
      apiClient.post<ApiEnvelope<{ cancelled_count: number }>>(
        '/api/v1/jobs/cancellation-requests',
        { job_ids: chunk },
      )),
  );
  return {
    cancelled_count: responses.reduce(
      (total, response) => total + response.data.data.cancelled_count,
      0,
    ),
  };
}
