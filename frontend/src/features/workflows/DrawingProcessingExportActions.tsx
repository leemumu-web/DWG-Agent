import { Space } from 'antd';
import { DrawingSelectiveExportControl } from './DrawingSelectiveExportControl';
import { WorkflowBatchExportControl } from './WorkflowBatchExportControl';

export function DrawingProcessingExportActions({
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
  return (
    <Space wrap>
      <DrawingSelectiveExportControl
        workflowId={workflowId}
        runId={runId}
        disabled={
          active
          || runId === undefined
          || !['completed', 'completed_with_review'].includes(runStatus ?? '')
        }
      />
      <WorkflowBatchExportControl
        workflowId={workflowId}
        disabled={active}
        onPurged={onPurged}
      />
    </Space>
  );
}
