import type { StoredFile } from '../files';
import type { Job } from '../jobs';

export const ACTIVE_JOB_STATUSES = new Set([
  'pending',
  'queued',
  'running',
  'validating',
  'waiting_cad_worker',
]);

export function isStuckJob(job: Job, now = Date.now()): boolean {
  return job.status === 'queued'
    && job.progress === 0
    && now - new Date(job.created_at).getTime() > 60_000;
}

export function actionableFiles(
  files: StoredFile[],
  jobsByFileId: Map<number, Job>,
): StoredFile[] {
  const now = Date.now();
  return files.filter((file) => {
    const job = jobsByFileId.get(file.id);
    return !job
      || job.status === 'failed'
      || job.status === 'cancelled'
      || (job.status === 'succeeded' && job.result_available === false)
      || isStuckJob(job, now);
  });
}
