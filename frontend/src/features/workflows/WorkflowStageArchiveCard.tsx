import { DownloadOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { App, Button, Card, Space, Tag, Typography } from 'antd';
import { useState } from 'react';

import { describeApiError, type TransferProgress } from '../../shared/api';
import { TransferProgressBar } from '../../shared/components';
import type { WorkflowArtifact, WorkflowStage } from './workflow';
import {
  downloadWorkflowExcelStageResult,
  downloadWorkflowExcelStage2ReaderResult,
  downloadWorkflowExcelStage2Result,
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
  const [readerDownloadProgress, setReaderDownloadProgress] = useState<TransferProgress | null>(null);
  const archiveM = useMutation({
    mutationFn: () => stage.stage_code === 'excel_stage1'
      ? downloadWorkflowExcelStageResult(workflowId, setDownloadProgress)
      : downloadWorkflowStageArchive(workflowId, stage.stage_code, setDownloadProgress),
    onError: (error) => message.error(describeApiError(
      error,
      stage.stage_code === 'excel_stage1' ? 'Excel 结果下载失败' : '阶段结果压缩包下载失败',
    )),
  });
  const readerM = useMutation({
    mutationFn: () => downloadWorkflowExcelStage2ReaderResult(workflowId, setReaderDownloadProgress),
    onMutate: () => setReaderDownloadProgress(null),
    onSuccess: () => message.success('BH 左右进读取表已下载'),
    onError: (error) => message.error(describeApiError(error, 'BH 左右进读取表下载失败')),
  });
  const stage2M = useMutation({
    mutationFn: () => downloadWorkflowExcelStage2Result(workflowId, setDownloadProgress),
    onMutate: () => setDownloadProgress(null),
    onSuccess: () => message.success('Excel 第二阶段结果已下载'),
    onError: (error) => message.error(describeApiError(error, 'Excel 第二阶段结果下载失败')),
  });
  const readerAvailable = artifacts.some((artifact) => artifact.artifact_type === 'bh_setback_excel');
  const stage2Available = artifacts.some((artifact) => artifact.artifact_type === 'stage2_excel');
  if (stage.stage_code === 'excel_stage2') {
    return (
      <Card className="workflow-stage-archive-card workflow-excel-stage2-downloads">
        <div>
          <span>第二阶段产物</span>
          <Typography.Text strong>
            {stage2Available
              ? '已生成第二阶段正式 Excel'
              : readerAvailable
                ? '已生成 BH 左右进读取表，可先下载核对'
                : '本阶段尚无可下载产物'}
          </Typography.Text>
          <Typography.Text type="secondary">
            两份结果必须单独下载，不以 ZIP 混合返回。
          </Typography.Text>
        </div>
        <Space wrap>
          <Button
            icon={<DownloadOutlined />}
            loading={readerM.isPending}
            disabled={!readerAvailable}
            onClick={() => readerM.mutate()}
          >
            下载 BH 左右进读取表
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={stage2M.isPending}
            disabled={!stage2Available}
            onClick={() => stage2M.mutate()}
          >
            下载 Excel 第二阶段结果
          </Button>
        </Space>
        {readerDownloadProgress && (
          <TransferProgressBar label="BH 左右进读取表下载" progress={readerDownloadProgress} />
        )}
        {downloadProgress && (
          <TransferProgressBar label="Excel 第二阶段结果下载" progress={downloadProgress} />
        )}
      </Card>
    );
  }
  const downloadLabel = stage.stage_code === 'dxf_classification'
    ? '下载分流结果压缩包'
    : stage.stage_code === 'excel_stage1'
      ? '下载 Excel 结果'
      : '下载本阶段结果压缩包';
  return (
    <Card className="workflow-stage-archive-card">
      <div>
        <span>阶段产物</span>
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
