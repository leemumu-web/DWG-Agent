import { Progress, Typography } from 'antd';

import type { Job } from './job';

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  queued: '排队中',
  running: '处理中',
  waiting_cad_worker: '等待 CAD 处理',
  validating: '校验中',
  succeeded: '已完成',
  failed: '处理失败',
  cancelled: '已取消',
};

function boundedPercent(value: number | undefined) {
  return Math.max(0, Math.min(100, Number.isFinite(value) ? Number(value) : 0));
}

function progressText(job: Job, key: 'phase_label' | 'message') {
  const value = job.progress_data?.[key];
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

export interface JobProgressPresentation {
  percent: number;
  milestonePercent: number;
  label: string;
  message?: string;
  indeterminate: boolean;
  terminal: boolean;
}

export function jobProgressPresentation(job: Job): JobProgressPresentation {
  const terminal = TERMINAL.has(job.status);
  const milestonePercent = boundedPercent(job.progress);
  return {
    // Keep the last confirmed milestone when a task fails or is cancelled.
    // Batch completion counts terminal files separately.
    percent: milestonePercent,
    milestonePercent,
    label: progressText(job, 'phase_label') ?? STATUS_LABELS[job.status] ?? '状态待确认',
    message: progressText(job, 'message'),
    indeterminate: job.progress_data?.indeterminate === true,
    terminal,
  };
}

export function JobProgressBar({
  job,
  width,
  showLabel = true,
}: {
  job: Job;
  width?: number | string;
  showLabel?: boolean;
}) {
  const view = jobProgressPresentation(job);
  const failed = job.status === 'failed' || job.status === 'cancelled';
  const status = failed
    ? 'exception'
    : job.status === 'succeeded'
      ? 'success'
      : view.indeterminate || job.status === 'running'
        ? 'active'
        : 'normal';
  return (
    <div style={{ width: width ?? '100%', minWidth: 0 }}>
      <Progress
        percent={view.percent}
        size="small"
        status={status}
        format={() => (
          failed
            ? '已终止'
            : view.indeterminate
              ? '处理中'
              : `${view.percent}%`
        )}
      />
      {showLabel && (
        <Typography.Text
          type="secondary"
          title={view.message}
          ellipsis
          style={{ display: 'block', fontSize: 12, marginTop: -4 }}
        >
          {view.label}
          {!view.terminal && view.milestonePercent > 0 ? ` · 已确认 ${view.milestonePercent}%` : ''}
        </Typography.Text>
      )}
    </div>
  );
}
