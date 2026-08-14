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
  // 60s 是「queued 且 progress=0」的卡死判定窗口（前端可见的最小阈值）：
  // 允许用户重新提交。注意服务器端 Job 可能仍存活在队列中，重新提交
  // 会产生重复任务——服务端靠幂等/attempt 收敛，此处的判定只是提示。
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
