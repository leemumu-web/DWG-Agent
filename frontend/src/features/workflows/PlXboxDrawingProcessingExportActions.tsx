import { Space } from 'antd';
import { PlXboxDrawingSelectiveExportControl } from './PlXboxDrawingSelectiveExportControl';
import { WorkflowBatchExportControl } from './WorkflowBatchExportControl';

export function PlXboxDrawingProcessingExportActions({
  workflowId,
  runId,
  runStatus,
  active,
  onPurged,
}: {
  workflowId: number;
  runId?: number;
  runStatus?: string;
  active: boolean;
  onPurged: () => void;
}) {
  const selectiveDisabledReason = active
    ? 'PL / XBOX 拆板任务正在执行，完成后才能按分类导出'
    : runId === undefined
      ? '尚未生成 PL / XBOX 拆板批次，请先完成整批拆板'
      : !['completed', 'completed_with_review'].includes(runStatus ?? '')
        ? '当前 PL / XBOX 拆板批次尚未形成可导出的分类结果'
        : undefined;
  return (
    <Space wrap>
      <PlXboxDrawingSelectiveExportControl
        workflowId={workflowId}
        runId={runId}
        disabled={selectiveDisabledReason !== undefined}
        disabledReason={selectiveDisabledReason}
      />
      <WorkflowBatchExportControl
        workflowId={workflowId}
        disabled={active}
        disabledReason={active ? 'PL / XBOX 拆板任务正在执行，完成后才能导出或清理生产文件' : undefined}
        onPurged={onPurged}
      />
    </Space>
  );
}
