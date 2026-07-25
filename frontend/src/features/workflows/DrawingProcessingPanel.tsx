import { useEffect, useRef } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Progress,
  Space,
  Typography,
} from 'antd';
import {
  DownloadOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { describeApiError } from '../../shared/api';
import type { WorkflowStage } from './workflow';
import {
  downloadDxfSplitManualReviewArchive,
  executeWorkflowStage,
  getDxfSplitRun,
  getWorkflow,
} from './workflows.api';

interface Props {
  workflowId: number;
  stage?: WorkflowStage;
  isCurrent: boolean;
  onChanged: () => void;
}

export function DrawingProcessingPanel({
  workflowId,
  stage,
  isCurrent,
  onChanged,
}: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const notifiedTerminalRun = useRef<string | undefined>(undefined);
  const runQ = useQuery({
    queryKey: ['workflow-dxf-split', workflowId],
    queryFn: () => getDxfSplitRun(workflowId),
    enabled: isCurrent || Boolean(stage?.job_id),
    refetchInterval: (query) => {
      const runStatus = query.state.data?.status;
      return runStatus === 'running'
        || stage?.status === 'queued'
        || stage?.status === 'running'
        ? 2000
        : false;
    },
  });
  const run = runQ.data;
  useEffect(() => {
    if (
      !run
      || !isCurrent
      || !['queued', 'running'].includes(stage?.status ?? '')
      || !['completed', 'completed_with_review', 'failed'].includes(run.status)
    ) return;
    const key = `${run.id}:${run.job.attempt}:${run.status}`;
    if (notifiedTerminalRun.current === key) return;
    notifiedTerminalRun.current = key;
    onChanged();
  }, [isCurrent, onChanged, run, stage?.status]);
  const active = run?.status === 'running'
    || stage?.status === 'queued'
    || stage?.status === 'running';
  const canExecute = isCurrent
    && !runQ.isError
    && !run
    && ['waiting_input', 'ready'].includes(stage?.status ?? '');

  const executeM = useMutation({
    mutationFn: async () => {
      const workflow = await getWorkflow(workflowId);
      if (!isCurrent || workflow.current_stage !== 'drawing_processing') {
        throw new Error('当前阶段已变化，请刷新后重试');
      }
      return executeWorkflowStage(workflowId, 'drawing_processing', {
        execution_kind: 'drawing_processing',
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['workflow-dxf-split', workflowId],
      });
      onChanged();
    },
    onError: (error) => {
      message.error(describeApiError(error, '拆板任务提交失败'));
      onChanged();
    },
  });
  const reviewArchiveM = useMutation({
    mutationFn: () => {
      if (!run) throw new Error('拆板批次尚未生成');
      return downloadDxfSplitManualReviewArchive(workflowId, run.id);
    },
    onError: (error) => message.error(
      describeApiError(error, '未通过原图压缩包下载失败'),
    ),
  });

  return (
    <Card
      className="workflow-dxf-split-panel"
      title="03 · 图纸拆板与独立校验"
      style={{ marginTop: 12 }}
    >
      {runQ.isError && (
        <Alert
          type="error"
          showIcon
          message="拆板批次读取失败"
          description={describeApiError(runQ.error, '请刷新后重试')}
          action={(
            <Button
              icon={<ReloadOutlined />}
              loading={runQ.isFetching}
              onClick={() => runQ.refetch()}
            >
              重新读取
            </Button>
          )}
        />
      )}

      {canExecute && (
        <Alert
          type="info"
          showIcon
          message="分类 DXF 已就绪"
          description="系统按冻结批次一次处理全部图纸，并分别登记正常拆板和余量增长 DXF。"
          action={(
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={executeM.isPending}
              onClick={() => executeM.mutate()}
            >
              开始整批拆板
            </Button>
          )}
        />
      )}

      {active && (
        <Alert
          type="info"
          showIcon
          message={`拆板任务${stage?.job_id ? ` #${stage.job_id}` : ''}正在执行`}
          description={<Progress percent={stage?.progress ?? run?.job.progress ?? 0} status="active" />}
          action={(
            <Button
              icon={<ReloadOutlined />}
              loading={runQ.isFetching}
              onClick={() => runQ.refetch()}
            >
              刷新
            </Button>
          )}
        />
      )}

      {run?.status === 'failed' && (
        <Alert
          type="error"
          showIcon
          message={`${run.error_code ?? 'DXF_SPLIT_FAILED'} · 拆板失败`}
          description={run.error_message ?? '系统已完成自动重试，请联系维护人员核对任务记录。'}
        />
      )}

      {run && ['completed', 'completed_with_review'].includes(run.status) && (
        <>
          <div className="workflow-dxf-split-command">
            <div>
              <Typography.Text strong>
                Steel DXF Split {run.splitter_version}
              </Typography.Text>
              <Typography.Text type="secondary">
                尝试 #{run.job.attempt} · 输入清单 {run.input_manifest_sha256.slice(0, 12)}…
              </Typography.Text>
            </div>
            <Button
              icon={<ReloadOutlined />}
              loading={runQ.isFetching}
              onClick={() => runQ.refetch()}
            >
              刷新
            </Button>
          </div>
          <div className="workflow-dxf-split-summary">
            {[
              ['本批次图纸', run.input_count],
              ['自动完成', run.auto_accepted_count],
              ['待人工处理', run.manual_review_count],
            ].map(([label, value]) => (
              <div key={label}>
                <small>{label}</small>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          {run.status === 'completed' ? (
            <Alert
              type="success"
              showIcon
              message="本批次拆板与独立校验已全部通过"
              description="正常拆板 DXF 将作为后续 Excel/CAM 默认图纸，余量增长 DXF 同步保留为正式产物。"
            />
          ) : (
            <Alert
              type="warning"
              showIcon
              message={`${run.manual_review_count} 张图纸未通过自动处理`}
              description="当前阶段保持待复核；压缩包只包含这些图纸进入拆板前的分类原始 DXF。"
              action={(
                <Space>
                  <Button
                    type="primary"
                    icon={<DownloadOutlined />}
                    loading={reviewArchiveM.isPending}
                    onClick={() => reviewArchiveM.mutate()}
                  >
                    下载未通过原图 ZIP
                  </Button>
                </Space>
              )}
            />
          )}
        </>
      )}
    </Card>
  );
}
