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
import {
  describeApiError,
  describeDownloadError,
  operatorErrorMessage,
  useDownload,
  type TransferProgress,
} from '../../shared/api';
import { ApiErrorAlert, CancellableDownloadProgress } from '../../shared/components';
import type {
  PlXboxSplitItem,
  WorkflowBatchExport,
  WorkflowExportCategory,
  WorkflowStage,
} from './workflow';
import {
  createWorkflowBatchExport,
  downloadWorkflowBatchExport,
  executeWorkflowStage,
  getPlXboxSplitRun,
  getWorkflow,
  getWorkflowBatchExport,
} from './workflows.api';
import { PlXboxDrawingProcessingExportActions } from './PlXboxDrawingProcessingExportActions';

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
  // 导出下载状态机：prepared→downloading→downloaded / download_failed /
  // launchFailed。launch 后浏览器直收 ZIP 与服务器状态轮询并行；launchFailed
  // 表示浏览器侧失败需重试，cancel 只停浏览器侧、服务器文件保留；launch 后
  // 延迟 refetch 是为让服务器先落 downloaded 状态。失败可安全重试。
  const { message } = App.useApp();
  const downloadCtrl = useDownload();
  const cancelInFlight = downloadCtrl.cancel;
  const [created, setCreated] = useState<WorkflowBatchExport | null>(null);
  const [launchFailed, setLaunchFailed] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [progress, setProgress] = useState<TransferProgress | null>(null);
  const notifiedStatus = useRef<string | null>(null);
  useEffect(() => {
    cancelInFlight();
    setCreated(null);
    setLaunchFailed(false);
    setDownloading(false);
    setProgress(null);
    notifiedStatus.current = null;
  }, [cancelInFlight, workflowId]);
  const statusQ = useQuery({
    queryKey: ['workflow-native-export', workflowId, created?.export_uid],
    queryFn: () => getWorkflowBatchExport(workflowId, created!.export_uid),
    enabled: Boolean(created),
    refetchInterval: (query) => {
      if (launchFailed) return false;
      return ACTIVE_EXPORT_STATUSES.has(
        query.state.data?.status ?? created?.status ?? '',
      )
        ? 2000
        : false;
    },
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
  const launch = async (next: WorkflowBatchExport) => {
    setDownloading(true);
    setProgress(null);
    const handle = downloadCtrl.start();
    try {
      await downloadWorkflowBatchExport(next, setProgress, handle.signal);
      setLaunchFailed(false);
      setTimeout(() => { void statusQ.refetch(); }, 300);
    } catch (error) {
      setLaunchFailed(true);
      const result = describeDownloadError(error, errorText);
      if (result.cancelled) {
        message.info('下载已取消，服务器文件仍保留，可重新下载');
      } else {
        message.error(result.message);
      }
    } finally {
      setDownloading(false);
      handle.finish();
    }
  };
  const createM = useMutation({
    mutationFn: () => createWorkflowBatchExport(workflowId, categories),
    onSuccess: (next) => {
      setCreated(next);
      message.info(preparingText);
      void launch(next);
    },
    onError: (error) => message.error(describeApiError(error, errorText)),
  });
  const start = () => {
    if (!row) {
      createM.mutate();
      return;
    }
    if (!launchFailed && ACTIVE_EXPORT_STATUSES.has(row.status)) {
      message.info('浏览器仍在接收 ZIP，请查看浏览器下载列表');
      return;
    }
    notifiedStatus.current = null;
    message.info(preparingText);
    void launch(row);
    setTimeout(() => {
      void statusQ.refetch();
    }, 500);
  };
  const cancelDownload = () => {
    downloadCtrl.cancel();
    setProgress(null);
  };
  return {
    start,
    loading: createM.isPending
      || downloading
      || (!launchFailed && ACTIVE_EXPORT_STATUSES.has(row?.status ?? '')),
    failed: launchFailed || row?.status === 'download_failed',
    progress,
    cancel: cancelDownload,
    active: downloadCtrl.active,
  };
}

interface Props {
  workflowId: number;
  stage?: WorkflowStage;
  isCurrent: boolean;
  onChanged: () => void;
}

const IMPLEMENTED_FAMILIES = ['PL', 'XBOX'] as const;

function familyAcceptedCounts(items: PlXboxSplitItem[]) {
  return IMPLEMENTED_FAMILIES.map((family) => ({
    family,
    accepted: items.filter(
      (item) => item.family === family && item.automation_route === 'auto_accepted',
    ).length,
  }));
}

export function PlXboxDrawingProcessingPanel({
  workflowId,
  stage,
  isCurrent,
  onChanged,
}: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const notifiedTerminalRun = useRef<string | undefined>(undefined);
  const runQ = useQuery({
    queryKey: ['workflow-pl-xbox-split', workflowId],
    queryFn: () => getPlXboxSplitRun(workflowId),
    enabled: isCurrent || Boolean(stage?.job_id),
    refetchInterval: (query) => {
      const runStatus = query.state.data?.status;
      return runStatus === 'running'
        || stage?.status === 'queued'
        || stage?.status === 'running'
        ? 4000
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
    // 通知按 (run, attempt, status) 去重：key 刻意包含 job.attempt，
    // 保证同一 run 跨世代重跑时每个 attempt 只通知一次。
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
      if (!isCurrent || workflow.current_stage !== 'pl_xbox_split') {
        throw new Error('当前阶段已变化，请刷新后重试');
      }
      return executeWorkflowStage(workflowId, 'pl_xbox_split', {
        execution_kind: 'pl_xbox_split',
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['workflow-pl-xbox-split', workflowId],
      });
      await queryClient.invalidateQueries({
        queryKey: ['workflow', workflowId],
      });
      message.success('整批 PL 拆板任务已提交');
      onChanged();
    },
    onError: (error) => {
      message.error(describeApiError(error, 'PL 拆板任务提交失败'));
      onChanged();
    },
  });
  const splitResultsDownload = useNativeWorkflowDownload({
    workflowId,
    categories: ['split_result_normal'],
    preparingText: '浏览器已开始接收 PL 拆板结果 ZIP，可继续操作页面',
    completedText: 'PL 拆板结果 ZIP 已由服务器完整发送',
    errorText: 'PL 拆板正式结果压缩包下载失败',
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
  const familyAccepted = run ? familyAcceptedCounts(run.items) : [];
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
      title="PL / XBOX 拆板与独立校验"
      extra={<PlXboxDrawingProcessingExportActions workflowId={workflowId} runId={run?.id} runStatus={run?.status} active={active} onPurged={onChanged} />}
      style={{ marginTop: 12 }}
    >
      {runQ.isError && (
        <ApiErrorAlert
          title="PL 拆板批次读取失败"
          error={runQ.error}
          fallback="PL 拆板批次读取失败"
          retryLabel="重新读取"
          retryLoading={runQ.isFetching}
          onRetry={() => runQ.refetch()}
        />
      )}

      {canExecute && (
        <Alert
          type="info"
          showIcon
          message="PL 拆板输入已就绪"
          description="拆板器处理数据库中已明确分类为 PL 与 XBOX 的图纸；BH、BOX 和其他类型继续走各自阶段。"
          action={(
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={executeM.isPending}
              onClick={() => executeM.mutate()}
            >
              开始整批 PL 拆板
            </Button>
          )}
        />
      )}

      {active && (
        <Alert
          type="info"
          showIcon
          message={`PL 拆板任务${stage?.job_id ? ` #${stage.job_id}` : ''}正在执行`}
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
          message="图纸拆板未完成"
          description={operatorErrorMessage(
            run.error_code,
            run.error_message,
            '请进入本批图纸明细，按页面指出的缺失内容处理。',
          )}
        />
      )}

      {run && ['completed', 'completed_with_review'].includes(run.status) && (
        <>
          <div className="workflow-dxf-split-command">
            <div>
              <Typography.Text strong>
                Steel DXF Split PL {run.splitter_version}
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
              ['正式结果图纸', run.auto_accepted_count],
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
              message="本批次 PL 拆板与独立校验已全部通过"
              description={(
                <Space orientation="vertical" size={2}>
                  <Typography.Text>
                    {familyAccepted.map(({ family, accepted }) => `${family} 正式结果 ${accepted} 张`).join(' · ')}
                  </Typography.Text>
                  <Typography.Text type="secondary">
                    PL 每图产出原长 DXF（单产物）；XBOX 每图产出原长与焊接余量成对 DXF。
                  </Typography.Text>
                </Space>
              )}
              action={(
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<DownloadOutlined />}
                    loading={splitResultsDownload.loading}
                    disabled={run.auto_accepted_count === 0 || allDrawingsDownload.active}
                    onClick={splitResultsDownload.start}
                  >
                    {splitResultsDownload.failed ? '重试拆板结果 ZIP' : '下载拆板结果 ZIP'}
                  </Button>
                  <Button icon={<DownloadOutlined />} loading={allDrawingsDownload.loading} disabled={splitResultsDownload.active} onClick={allDrawingsDownload.start}>
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
                message={`${run.input_count} 张均已处理：${run.auto_accepted_count} 张形成 PL 正式结果，${unformedCount} 张未形成`}
                description={(
                  <Space orientation="vertical" size={2}>
                    <Typography.Text>
                      {familyAccepted.map(({ family, accepted }) => `${family} 正式结果 ${accepted} 张`).join(' · ')}
                    </Typography.Text>
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
                      disabled={run.auto_accepted_count === 0 || allDrawingsDownload.active}
                      onClick={splitResultsDownload.start}
                    >
                      {splitResultsDownload.failed ? '重试拆板结果 ZIP' : '下载拆板结果 ZIP'}
                    </Button>
                    <Button icon={<DownloadOutlined />} loading={allDrawingsDownload.loading} disabled={splitResultsDownload.active} onClick={allDrawingsDownload.start}>
                      {allDrawingsDownload.failed
                        ? '重试本批原图 ZIP'
                        : '下载本批原图 ZIP（不含拆板成品）'}
                    </Button>
                  </Space>
                )}
              />
            </>
          )}
          {splitResultsDownload.progress && (
            <CancellableDownloadProgress
              label="拆板结果下载"
              progress={splitResultsDownload.progress}
              active={splitResultsDownload.active}
              onCancel={splitResultsDownload.cancel}
            />
          )}
          {allDrawingsDownload.progress && (
            <CancellableDownloadProgress
              label="本批原图下载"
              progress={allDrawingsDownload.progress}
              active={allDrawingsDownload.active}
              onCancel={allDrawingsDownload.cancel}
            />
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
