import { DownloadOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { App, Button, Card, Space, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  describeDownloadError,
  useDownload,
  type TransferProgress,
} from '../../shared/api';
import { CancellableDownloadProgress } from '../../shared/components';
import type { WorkflowArtifact, WorkflowStage } from './workflow';
import {
  downloadWorkflowExcelStage2BoxReaderResult,
  downloadWorkflowExcelStage2ReaderResult,
  downloadWorkflowExcelStage2Result,
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
  const downloadCtrl = useDownload();
  const [downloadProgress, setDownloadProgress] = useState<TransferProgress | null>(null);
  const [readerDownloadProgress, setReaderDownloadProgress] = useState<TransferProgress | null>(null);

  const clearProgress = () => {
    setDownloadProgress(null);
    setReaderDownloadProgress(null);
  };

  const archiveM = useMutation({
    mutationFn: () => {
      const handle = downloadCtrl.start();
      const promise = stage.stage_code === 'excel_stage1'
        ? downloadWorkflowExcelStageResult(workflowId, setDownloadProgress, handle.signal)
        : downloadWorkflowStageArchive(workflowId, stage.stage_code, setDownloadProgress, handle.signal);
      return promise.finally(handle.finish);
    },
    onMutate: clearProgress,
    onError: (error) => {
      const result = describeDownloadError(
        error,
        stage.stage_code === 'excel_stage1' ? 'Excel 结果下载失败' : '阶段结果压缩包下载失败',
      );
      setDownloadProgress(null);
      setReaderDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });
  const readerM = useMutation({
    mutationFn: () => {
      const handle = downloadCtrl.start();
      return downloadWorkflowExcelStage2ReaderResult(
        workflowId,
        setReaderDownloadProgress,
        handle.signal,
      ).finally(handle.finish);
    },
    onMutate: clearProgress,
    onSuccess: () => message.success('BH 左右进读取表已下载'),
    onError: (error) => {
      const result = describeDownloadError(error, 'BH 左右进读取表下载失败');
      setDownloadProgress(null);
      setReaderDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });
  const boxReaderM = useMutation({
    mutationFn: () => {
      const handle = downloadCtrl.start();
      return downloadWorkflowExcelStage2BoxReaderResult(
        workflowId,
        setReaderDownloadProgress,
        handle.signal,
      ).finally(handle.finish);
    },
    onMutate: clearProgress,
    onSuccess: () => message.success('BOX 左右进读取表已下载'),
    onError: (error) => {
      const result = describeDownloadError(error, 'BOX 左右进读取表下载失败');
      setDownloadProgress(null);
      setReaderDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });
  const stage2M = useMutation({
    mutationFn: () => {
      const handle = downloadCtrl.start();
      return downloadWorkflowExcelStage2Result(workflowId, setDownloadProgress, handle.signal)
        .finally(handle.finish);
    },
    onMutate: clearProgress,
    onSuccess: () => message.success('Excel 第二阶段结果已下载'),
    onError: (error) => {
      const result = describeDownloadError(error, 'Excel 第二阶段结果下载失败');
      setDownloadProgress(null);
      setReaderDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });
  const readerAvailable = artifacts.some((artifact) => artifact.artifact_type === 'bh_setback_excel');
  const boxReaderAvailable = artifacts.some((artifact) => artifact.artifact_type === 'box_setback_excel');
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
                ? '已生成 BH/BOX 左右进读取表，可先下载核对'
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
            disabled={!readerAvailable || downloadCtrl.active}
            onClick={() => readerM.mutate()}
          >
            下载 BH 左右进读取表
          </Button>
          <Button
            icon={<DownloadOutlined />}
            loading={boxReaderM.isPending}
            disabled={!boxReaderAvailable || downloadCtrl.active}
            onClick={() => boxReaderM.mutate()}
          >
            下载 BOX 左右进读取表
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={stage2M.isPending}
            disabled={!stage2Available || downloadCtrl.active}
            onClick={() => stage2M.mutate()}
          >
            下载 Excel 第二阶段结果
          </Button>
        </Space>
        {readerDownloadProgress && (
          <CancellableDownloadProgress
            label="BH 左右进读取表下载"
            progress={readerDownloadProgress}
            active={downloadCtrl.active}
            onCancel={() => {
              downloadCtrl.cancel();
              setReaderDownloadProgress(null);
            }}
          />
        )}
        {downloadProgress && (
          <CancellableDownloadProgress
            label="Excel 第二阶段结果下载"
            progress={downloadProgress}
            active={downloadCtrl.active}
            onCancel={() => {
              downloadCtrl.cancel();
              setDownloadProgress(null);
            }}
          />
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
        <CancellableDownloadProgress
          label="阶段图纸结果下载"
          progress={downloadProgress}
          active={downloadCtrl.active}
          onCancel={() => {
            downloadCtrl.cancel();
            setDownloadProgress(null);
          }}
        />
      )}
    </Card>
  );
}
