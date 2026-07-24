import { App, Button, Card, Tag, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useMemo } from 'react';

import { describeApiError } from '../../shared/api';
import { downloadWorkflowArchive } from './workflows.api';
import type { WorkflowArtifact } from './workflow';

export function WorkflowArtifactSummary({
  workflowId,
  artifacts,
}: {
  workflowId: number;
  artifacts: WorkflowArtifact[];
}) {
  const { message } = App.useApp();
  const groups = useMemo(() => {
    const counts = new Map<string, number>();
    artifacts.forEach((artifact) => {
      counts.set(artifact.artifact_type, (counts.get(artifact.artifact_type) ?? 0) + 1);
    });
    return Array.from(counts.entries()).sort(([left], [right]) => left.localeCompare(right));
  }, [artifacts]);
  const archiveM = useMutation({
    mutationFn: () => downloadWorkflowArchive(workflowId),
    onError: (error) => message.error(describeApiError(error, '生产压缩包下载失败')),
  });

  return (
    <Card className="workflow-artifact-summary">
      <div className="workflow-artifact-summary__heading">
        <div>
          <Typography.Title level={4}>生产产物与证据</Typography.Title>
          <Typography.Text type="secondary">
            {artifacts.length ? `已登记 ${artifacts.length} 项` : '暂无已登记产物'}
          </Typography.Text>
        </div>
        <Button
          icon={<DownloadOutlined />}
          loading={archiveM.isPending}
          disabled={!artifacts.length}
          onClick={() => archiveM.mutate()}
        >
          下载全部
        </Button>
      </div>
      {groups.length > 0 && (
        <div className="workflow-artifact-summary__groups" aria-label="产物类型汇总">
          {groups.map(([artifactType, count]) => (
            <Tag key={artifactType}>{artifactType} × {count}</Tag>
          ))}
        </div>
      )}
    </Card>
  );
}
