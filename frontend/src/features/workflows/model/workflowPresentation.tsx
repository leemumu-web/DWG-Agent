import { Tag } from 'antd';

import type { StatusStyle } from '../../../shared/components';
import type { WorkflowStageCapability } from '../workflow';

export const WORKFLOW_STATUS: Record<string, StatusStyle> = {
  draft: { color: '#667085', bg: '#f8fafc', border: '#e2e8f0', label: '草稿' },
  waiting_input: { color: '#7c3aed', bg: '#f5f3ff', border: '#ddd6fe', label: '待输入' },
  running: { color: '#2563eb', bg: '#eff6ff', border: '#bfdbfe', label: '进行中' },
  waiting_review: { color: '#d97706', bg: '#fffbeb', border: '#fde68a', label: '待确认' },
  succeeded: { color: '#059669', bg: '#ecfdf5', border: '#a7f3d0', label: '已完成' },
  skipped: { color: '#059669', bg: '#ecfdf5', border: '#a7f3d0', label: '无需处理' },
  failed: { color: '#dc2626', bg: '#fef2f2', border: '#fecaca', label: '失败' },
  cancelled: { color: '#667085', bg: '#f8fafc', border: '#e2e8f0', label: '已取消' },
};

export const STAGE_STATUS: Record<string, 'wait' | 'process' | 'finish' | 'error'> = {
  pending: 'wait',
  ready: 'process',
  waiting_input: 'process',
  waiting_review: 'process',
  queued: 'process',
  running: 'process',
  succeeded: 'finish',
  skipped: 'finish',
  failed: 'error',
  cancelled: 'error',
};

export const TERMINAL = new Set(['succeeded', 'skipped', 'failed', 'cancelled']);
export const ACTIONABLE = new Set(['ready', 'waiting_input', 'waiting_review']);
export const WAITING_LAUNCH_STAGES = new Set([
  'excel_stage2',
  'cam_packaging',
  'windows_cam',
  'result_acceptance',
  'delivery_archive',
]);

export function isWaitingLaunchStage(stageCode?: string): boolean {
  return Boolean(stageCode && WAITING_LAUNCH_STAGES.has(stageCode));
}

export function capabilityTag(capability?: WorkflowStageCapability, stageCode?: string) {
  if (!capability) return null;
  if (isWaitingLaunchStage(stageCode)) {
    return <Tag>等待上线</Tag>;
  }
  if (capability.implementation_status === 'implemented') {
    return <Tag color="success">服务器已实现</Tag>;
  }
  if (capability.implementation_status === 'external') {
    return <Tag color="processing">外部节点接口</Tag>;
  }
  return <Tag color="warning">核心接口留白</Tag>;
}
