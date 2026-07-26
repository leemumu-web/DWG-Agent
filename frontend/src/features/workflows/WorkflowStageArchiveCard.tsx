import { DownloadOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { App, Button, Card, Space, Tag, Typography } from 'antd';
import { useState } from 'react';

import { describeApiError, type TransferProgress } from '../../shared/api';
import { TransferProgressBar } from '../../shared/components';
import type { WorkflowArtifact, WorkflowStage } from './workflow';
import {
  downloadWorkflowExcelStageResult,
  downloadWorkflowStageArchive,
} from './workflows.api';

export function WorkflowStageArchiveCard({
  workflowId,
  stage,
  artifacts,
}: {
  workflowId: number;
  stage: WorkflowStage;
  artifacts: WorkflowArtifact[];
}) {
  const { message } = App.useApp();
  const [downloadProgress, setDownloadProgress] = useState<TransferProgress | null>(null);
  const archiveM = useMutation({
    mutationFn: () => stage.stage_code === 'excel_stage1'
      ? downloadWorkflowExcelStageResult(workflowId, setDownloadProgress)
      : downloadWorkflowStageArchive(workflowId, stage.stage_code, setDownloadProgress),
    onError: (error) => message.error(describeApiError(
      error,
      stage.stage_code === 'excel_stage1' ? 'Excel 结果下载失败' : '阶段结果压缩包下载失败',
    )),
  });
  const downloadLabel = stage.stage_code === 'dxf_classification'
    ? '下载分流结果压缩包'
    : stage.stage_code === 'excel_stage1'
      ? '下载 Excel 结果'
      : '下载本阶段结果压缩包';
  return (
    <Card className="workflow-stage-archive-card">
      <div>
        <span>STAGE OUTPUT</span>
        <Typography.Text strong>
          {artifacts.length
            ? stage.stage_code === 'excel_stage1'
              ? '已生成 1 个 Excel 文件'
              : `已登记 ${artifacts.length} 项阶段产物`
            : '本阶段尚无可下载产物'}
        </Typography.Text>
        {artifacts.length > 0 && (
          <Space wrap size={[4, 6]}>
            {Array.from(new Set(artifacts.map((artifact) => artifact.artifact_type)))
              .map((artifactType) => <Tag key={artifactType}>{artifactType}</Tag>)}
          </Space>
        )}
      </div>
      <Button
        type={stage.stage_code === 'dxf_classification' ? 'primary' : 'default'}
        icon={<DownloadOutlined />}
        loading={archiveM.isPending}
        disabled={!artifacts.length}
        onClick={() => archiveM.mutate()}
      >
        {downloadLabel}
      </Button>
      {downloadProgress && stage.stage_code !== 'excel_stage1' && (
        <TransferProgressBar label="阶段图纸结果下载" progress={downloadProgress} />
      )}
    </Card>
  );
}
