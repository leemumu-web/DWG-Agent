import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Progress,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  DownloadOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { describeApiError } from '../../shared/api';
import type {
  WorkflowBatchExport,
  WorkflowExportCategory,
  WorkflowStage,
} from './workflow';
import {
  createWorkflowBatchExport,
  executeWorkflowStage,
  getDxfSplitRun,
  getWorkflowBatchExport,
  getWorkflow,
  startNativeWorkflowBatchExportDownload,
} from './workflows.api';
import { WorkflowBatchExportControl } from './WorkflowBatchExportControl';

const ACTIVE_EXPORT_STATUSES = new Set(['prepared', 'downloading']);

function useNativeWorkflowDownload({
  workflowId,
  categories,
  preparingText,
  completedText,
  errorText,
}: {
  workflowId: number;
  categories: WorkflowExportCategory[];
  preparingText: string;
  completedText: string;
  errorText: string;
}) {
  const { message } = App.useApp();
  const [created, setCreated] = useState<WorkflowBatchExport | null>(null);
  const notifiedStatus = useRef<string | null>(null);
  useEffect(() => {
    setCreated(null);
    notifiedStatus.current = null;
  }, [workflowId]);
  const statusQ = useQuery({
    queryKey: ['workflow-native-export', workflowId, created?.export_uid],
    queryFn: () => getWorkflowBatchExport(workflowId, created!.export_uid),
    enabled: Boolean(created),
    refetchInterval: (query) => (
      ACTIVE_EXPORT_STATUSES.has(
        query.state.data?.status ?? created?.status ?? '',
      )
        ? 1000
        : false
    ),
  });
  const row = statusQ.data ?? created;
  useEffect(() => {
    if (!row || !['downloaded', 'download_failed'].includes(row.status)) return;
    const key = `${row.export_uid}:${row.status}`;
    if (notifiedStatus.current === key) return;
    notifiedStatus.current = key;
    if (row.status === 'downloaded') {
      message.success(completedText);
    } else {
      message.error(`${errorText}；服务器文件仍保留，可点击按钮重试`);
    }
  }, [completedText, errorText, message, row]);
  const createM = useMutation({
    mutationFn: () => createWorkflowBatchExport(workflowId, categories),
    onSuccess: (next) => {
      setCreated(next);
      startNativeWorkflowBatchExportDownload(next);
      message.info(preparingText);
    },
    onError: (error) => message.error(describeApiError(error, errorText)),
  });
  const start = () => {
    if (!row) {
      createM.mutate();
      return;
    }
    if (ACTIVE_EXPORT_STATUSES.has(row.status)) {
      message.info('浏览器仍在接收 ZIP，请查看浏览器下载列表');
      return;
    }
    try {
      notifiedStatus.current = null;
      startNativeWorkflowBatchExportDownload(row);
      message.info(preparingText);
      setTimeout(() => {
        void statusQ.refetch();
      }, 500);
    } catch (error) {
      message.error(describeApiError(error, errorText));
    }
  };
  return {
    start,
    loading: createM.isPending || ACTIVE_EXPORT_STATUSES.has(row?.status ?? ''),
    failed: row?.status === 'download_failed',
  };
}

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
      || !['completed', 'completed_with_review', 'failed'].includes(run.status)
    ) return;
    const key = `${run.id}:${run.job.attempt}:${run.status}`;
    if (notifiedTerminalRun.current === key) return;
    notifiedTerminalRun.current = key;
    void queryClient.invalidateQueries({
      queryKey: ['workflow', workflowId],
    });
    void queryClient.invalidateQueries({ queryKey: ['workflows'] });
    onChanged();
  }, [isCurrent, onChanged, queryClient, run, workflowId]);
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
      await queryClient.invalidateQueries({
        queryKey: ['workflow', workflowId],
      });
      message.success('整批拆板任务已提交');
      onChanged();
    },
    onError: (error) => {
      message.error(describeApiError(error, '拆板任务提交失败'));
      onChanged();
    },
  });
  const splitResultsDownload = useNativeWorkflowDownload({
    workflowId,
    categories: ['split_result_normal', 'split_result_allowance'],
    preparingText: '浏览器已开始接收拆板结果 ZIP，可继续操作页面',
    completedText: '拆板结果 ZIP 已由服务器完整发送',
    errorText: '拆板正式结果压缩包下载失败',
  });
  const allDrawingsDownload = useNativeWorkflowDownload({
    workflowId,
    categories: ['classified_dxf'],
    preparingText: '浏览器已开始接收本批原图 ZIP，可继续操作页面',
    completedText: '本批原图 ZIP 已由服务器完整发送',
    errorText: '本批原图压缩包下载失败',
  });
  const processedCount = run?.processed_count ?? 0;
  const unformedCount = run
    ? Math.max(0, run.input_count - run.auto_accepted_count)
    : 0;
  const unformedReasons = run
    ? Array.from(new Set(
      run.items
        .filter((item) => item.automation_route !== 'auto_accepted')
        .map((item) => {
          const checks = item.validation?.checks;
          if (!checks || typeof checks !== 'object') return null;
          const reason = (checks as Record<string, unknown>).error_zh;
          return typeof reason === 'string' && reason ? reason : null;
        })
        .filter((value): value is string => Boolean(value)),
    ))
    : [];
  const progressPercent = run && run.input_count > 0
    ? Math.min(100, Math.round((processedCount / run.input_count) * 100))
    : (stage?.progress ?? run?.job.progress ?? 0);
  const speedText = run?.throughput_per_minute == null
    ? '速度计算中'
    : `${run.throughput_per_minute.toFixed(1)} 张/分钟`;
  const etaText = run?.estimated_remaining_seconds == null
    ? '预计剩余时间计算中'
    : `预计剩余 ${Math.max(0, Math.ceil(run.estimated_remaining_seconds / 60))} 分钟`;
  return (
    <Card
      className="workflow-dxf-split-panel"
      title="03 · 图纸拆板与独立校验"
      extra={<WorkflowBatchExportControl workflowId={workflowId} disabled={active} onPurged={onChanged} />}
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
          message="BH / BOX 拆板输入已就绪"
          description="拆板器当前只处理数据库中已明确分类为 BH 或 BOX 的图纸。PX、其他类型和未分类图纸保留分类标注，本节点暂不拆板，也不会阻塞 BH/BOX 批次。"
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
          description={(
            <div className="workflow-dxf-split-progress">
              <Progress percent={progressPercent} status="active" />
              <div>
                <Typography.Text strong>
                  总进度 {processedCount}/{run?.input_count ?? '—'} 张
                </Typography.Text>
                <Typography.Text type="secondary">
                  {speedText} · {etaText}
                </Typography.Text>
              </div>
              {run && (
                <Space size={6} wrap>
                  <Tag color="success">已形成生产结果 {run.auto_accepted_count}</Tag>
                </Space>
              )}
            </div>
          )}
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
          description={run.error_message ?? '请按错误中指出的图纸和缺失内容人工处理。'}
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
              ['分类总数', run.classification_input_count],
              ['进入拆板', run.input_count],
              ['仅分类未拆', run.classification_only_count],
              ['正式配对图纸', run.auto_accepted_count],
              ['未形成结果', unformedCount],
            ].map(([label, value]) => (
              <div key={label}>
                <small>{label}</small>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          {run.classification_only_count > 0 && (
            <Alert
              type="info"
              showIcon
              message={`${run.classification_only_count} 张图纸仅保留分类，本节点不拆板`}
              description={Object.entries(run.classification_only_type_counts)
                .map(([type, count]) => `${type} ${count}`)
                .join(' · ')}
            />
          )}
          {run.status === 'completed' ? (
            <Alert
              type="success"
              showIcon
              message="本批次拆板与独立校验已全部通过"
              description="本次进入拆板的 BH/BOX 均已通过独立校验；其他类型只保留分类标注，不包含在拆板结果中。"
              action={(
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<DownloadOutlined />}
                    loading={splitResultsDownload.loading}
                    disabled={run.auto_accepted_count === 0}
                    onClick={splitResultsDownload.start}
                  >
                    {splitResultsDownload.failed ? '重试拆板结果 ZIP' : '下载拆板结果 ZIP'}
                  </Button>
                  <Button icon={<DownloadOutlined />} loading={allDrawingsDownload.loading} onClick={allDrawingsDownload.start}>
                    {allDrawingsDownload.failed
                      ? '重试本批原图 ZIP'
                      : '下载本批原图 ZIP（不含拆板成品）'}
                  </Button>
                </Space>
              )}
            />
          ) : (
            <>
              <Alert
                type="warning"
                showIcon
                message={`${run.input_count} 张均已处理：${run.auto_accepted_count} 张形成正式配对结果，${unformedCount} 张未形成`}
                description={(
                  <Space orientation="vertical" size={2}>
                    {unformedReasons.map((reason) => (
                      <Typography.Text key={reason}>{reason}</Typography.Text>
                    ))}
                    <Typography.Text type="secondary">
                      未形成结果的图纸不进入正式拆板 ZIP；本批原图包保留全部分类图纸，可供线下处理。
                    </Typography.Text>
                  </Space>
                )}
                action={(
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<DownloadOutlined />}
                      loading={splitResultsDownload.loading}
                      disabled={run.auto_accepted_count === 0}
                      onClick={splitResultsDownload.start}
                    >
                      {splitResultsDownload.failed ? '重试拆板结果 ZIP' : '下载拆板结果 ZIP'}
                    </Button>
                    <Button icon={<DownloadOutlined />} loading={allDrawingsDownload.loading} onClick={allDrawingsDownload.start}>
                      {allDrawingsDownload.failed
                        ? '重试本批原图 ZIP'
                        : '下载本批原图 ZIP（不含拆板成品）'}
                    </Button>
                  </Space>
                )}
              />
            </>
          )}
          {run.items.length > 0 && (
            <details className="workflow-dxf-split-ledger">
              <summary>
                逐图拆板与独立校验账本
                <Typography.Text type="secondary">{run.items.length} 张</Typography.Text>
              </summary>
              <div>
                {run.items.map((item) => (
                  <article key={item.id}>
                    <Typography.Text strong ellipsis={{ tooltip: item.source_name }}>
                      {item.source_name}
                    </Typography.Text>
                    <Space size={6} wrap>
                      <Tag>分类：{item.classification_part_type ?? '未分类'}</Tag>
                      <Tag color={item.family ? 'cyan' : 'default'}>
                        拆板识别：{item.family ?? '未识别'}
                      </Tag>
                    </Space>
                    <Tag color={item.automation_route === 'auto_accepted' ? 'success' : 'warning'}>
                      {item.automation_route === 'auto_accepted'
                        ? '独立校验通过'
                        : '未形成正式结果'}
                    </Tag>
                  </article>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </Card>
  );
}
