import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Input,
  Modal,
  Pagination,
  Progress,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  DownloadOutlined,
  SafetyCertificateOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { describeApiError } from '../../shared/api';
import type {
  DxfSplitReviewDecisionKind,
  DxfSplitReviewItem,
  WorkflowStage,
} from './workflow';
import {
  completeDxfSplitReview,
  decideDxfSplitReviewItem,
  downloadDxfSplitManualReviewArchive,
  downloadDxfSplitResultsArchive,
  downloadDxfSplitReviewCandidatesArchive,
  executeWorkflowStage,
  getDxfSplitReviewItems,
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
  const [reviewPage, setReviewPage] = useState(1);
  const [decisionDraft, setDecisionDraft] = useState<{
    item: DxfSplitReviewItem;
    decision: DxfSplitReviewDecisionKind;
  } | null>(null);
  const [decisionComment, setDecisionComment] = useState('');
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
  const reviewQ = useQuery({
    queryKey: ['workflow-dxf-split-review', workflowId, run?.id, reviewPage],
    queryFn: () => getDxfSplitReviewItems(
      workflowId,
      run!.id,
      reviewPage,
      20,
    ),
    enabled: isCurrent && run?.status === 'completed_with_review',
  });

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
      message.success(run?.status === 'completed_with_review'
        ? '新一轮整批拆板已提交'
        : '整批拆板任务已提交');
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
  const candidateArchiveM = useMutation({
    mutationFn: () => {
      if (!run) throw new Error('拆板批次尚未生成');
      return downloadDxfSplitReviewCandidatesArchive(workflowId, run.id);
    },
    onError: (error) => message.error(
      describeApiError(error, '候选复核压缩包下载失败'),
    ),
  });
  const resultsArchiveM = useMutation({
    mutationFn: () => {
      if (!run) throw new Error('拆板批次尚未生成');
      return downloadDxfSplitResultsArchive(workflowId, run.id);
    },
    onError: (error) => message.error(
      describeApiError(error, '拆板正式结果压缩包下载失败'),
    ),
  });
  const decisionM = useMutation({
    mutationFn: async ({
      item,
      decision,
      comment,
    }: {
      item: DxfSplitReviewItem;
      decision: DxfSplitReviewDecisionKind;
      comment: string;
    }) => {
      if (!run || !isCurrent) throw new Error('当前拆板阶段已变化，请刷新后重试');
      const workflow = await getWorkflow(workflowId);
      if (workflow.current_stage !== 'drawing_processing') {
        throw new Error('当前拆板阶段已变化，请刷新后重试');
      }
      return decideDxfSplitReviewItem(workflowId, run.id, item.id, {
        decision,
        comment,
        expected_version: item.decision?.version ?? 0,
      });
    },
    onSuccess: async () => {
      setDecisionDraft(null);
      setDecisionComment('');
      await queryClient.invalidateQueries({
        queryKey: ['workflow-dxf-split-review', workflowId],
      });
      message.success('人工复核决定已保存');
    },
    onError: (error) => message.error(
      describeApiError(error, '人工复核决定保存失败'),
    ),
  });
  const completeReviewM = useMutation({
    mutationFn: async () => {
      if (!run || !isCurrent) throw new Error('当前拆板阶段已变化，请刷新后重试');
      const workflow = await getWorkflow(workflowId);
      if (workflow.current_stage !== 'drawing_processing') {
        throw new Error('当前拆板阶段已变化，请刷新后重试');
      }
      return completeDxfSplitReview(workflowId, run.id);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ['workflow-dxf-split', workflowId],
        }),
        queryClient.invalidateQueries({
          queryKey: ['workflow-dxf-split-review', workflowId],
        }),
      ]);
      message.success('拆板复核已完成，已进入 Excel 阶段');
      onChanged();
    },
    onError: (error) => message.error(
      describeApiError(error, '拆板复核完成失败'),
    ),
  });
  const processedCount = run?.processed_count ?? 0;
  const progressPercent = run && run.input_count > 0
    ? Math.min(100, Math.round((processedCount / run.input_count) * 100))
    : (stage?.progress ?? run?.job.progress ?? 0);
  const speedText = run?.throughput_per_minute == null
    ? '速度计算中'
    : `${run.throughput_per_minute.toFixed(1)} 张/分钟`;
  const etaText = run?.estimated_remaining_seconds == null
    ? '预计剩余时间计算中'
    : `预计剩余 ${Math.max(0, Math.ceil(run.estimated_remaining_seconds / 60))} 分钟`;
  const review = reviewQ.data;
  const canCompleteReview = Boolean(
    isCurrent
    && review
    && review.pending_count === 0
    && review.manual_processing_count === 0,
  );

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
                  <Tag color="success">自动完成 {run.auto_accepted_count}</Tag>
                  <Tag color="warning">待人工 {run.manual_review_count}</Tag>
                  <Tag color="error">失败 {run.failed_count}</Tag>
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
              [
                run.status === 'completed' ? '人工复核' : '需人工复核',
                run.status === 'completed' ? run.reviewed_count : run.manual_review_count,
              ],
              ['失败', run.failed_count],
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
              action={(
                <Button
                  type="primary"
                  icon={<DownloadOutlined />}
                  loading={resultsArchiveM.isPending}
                  onClick={() => resultsArchiveM.mutate()}
                >
                  下载拆板结果 ZIP
                </Button>
              )}
            />
          ) : (
            <>
              <Alert
                type="warning"
                showIcon
                message={`${run.manual_review_count} 张图纸未通过自动处理`}
                description="下载候选复核 ZIP 集中核对原图、候选 DXF、校验报告和诊断清单；页面只保留必要的决定项。"
                action={(
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<SafetyCertificateOutlined />}
                      loading={candidateArchiveM.isPending}
                      onClick={() => candidateArchiveM.mutate()}
                    >
                      下载候选复核 ZIP
                    </Button>
                    <Button
                      icon={<DownloadOutlined />}
                      loading={reviewArchiveM.isPending}
                      onClick={() => reviewArchiveM.mutate()}
                    >
                      仅下载未通过原图 ZIP
                    </Button>
                    <Button
                      icon={<ThunderboltOutlined />}
                      loading={executeM.isPending}
                      disabled={!isCurrent}
                      onClick={() => executeM.mutate()}
                    >
                      重新整批拆板
                    </Button>
                  </Space>
                )}
              />
              {reviewQ.isError && (
                <Alert
                  type="error"
                  showIcon
                  message="复核条目读取失败"
                  description={describeApiError(reviewQ.error, '请刷新后重试')}
                />
              )}
              {review && (
                <section className="workflow-dxf-split-review">
                  <header>
                    <div>
                      <Typography.Title level={5}>人工复核决定</Typography.Title>
                      <Typography.Text type="secondary">
                        待决定 {review.pending_count} · 线下处理 {review.manual_processing_count}
                      </Typography.Text>
                    </div>
                    <Button
                      type="primary"
                      icon={<CheckCircleOutlined />}
                      disabled={!canCompleteReview}
                      loading={completeReviewM.isPending}
                      onClick={() => completeReviewM.mutate()}
                    >
                      完成复核并进入 Excel
                    </Button>
                  </header>
                  <div className="workflow-dxf-split-review-list">
                    {review.items.map((item) => (
                      <article key={item.id}>
                        <div className="workflow-dxf-split-review-main">
                          <Typography.Text strong ellipsis={{ tooltip: item.source_name }}>
                            {item.source_name}
                          </Typography.Text>
                          <Space size={6} wrap>
                            <Tag color="cyan">{item.part_type}</Tag>
                            {item.profile_normalized && <Tag>{item.profile_normalized}</Tag>}
                            {item.decision?.decision === 'accept_candidate' && (
                              <Tag color="success">已采用候选</Tag>
                            )}
                            {item.decision?.decision === 'manual_processing' && (
                              <Tag color="warning">需线下处理</Tag>
                            )}
                          </Space>
                          <Typography.Text type="secondary" ellipsis>
                            {item.diagnostics[0] ?? item.disposition}
                          </Typography.Text>
                        </div>
                        <Space wrap>
                          <Button
                            size="small"
                            type={item.decision?.decision === 'accept_candidate' ? 'primary' : 'default'}
                            disabled={!isCurrent || !item.candidate_available}
                            onClick={() => {
                              setDecisionDraft({ item, decision: 'accept_candidate' });
                              setDecisionComment(item.decision?.comment ?? '');
                            }}
                          >
                            采用候选
                          </Button>
                          <Button
                            size="small"
                            icon={<ToolOutlined />}
                            disabled={!isCurrent}
                            onClick={() => {
                              setDecisionDraft({ item, decision: 'manual_processing' });
                              setDecisionComment(item.decision?.comment ?? '');
                            }}
                          >
                            需人工处理
                          </Button>
                        </Space>
                      </article>
                    ))}
                  </div>
                  {review.total > review.page_size && (
                    <Pagination
                      size="small"
                      current={review.page}
                      pageSize={review.page_size}
                      total={review.total}
                      showSizeChanger={false}
                      onChange={setReviewPage}
                    />
                  )}
                </section>
              )}
            </>
          )}
        </>
      )}
      <Modal
        open={decisionDraft !== null}
        title={
          decisionDraft?.decision === 'accept_candidate'
            ? '确认采用候选 DXF'
            : '标记为需线下人工处理'
        }
        okText="保存决定"
        cancelText="取消"
        confirmLoading={decisionM.isPending}
        okButtonProps={{ disabled: decisionComment.trim().length < 2 }}
        onCancel={() => {
          if (!decisionM.isPending) {
            setDecisionDraft(null);
            setDecisionComment('');
          }
        }}
        onOk={() => {
          if (!decisionDraft || decisionComment.trim().length < 2) return;
          decisionM.mutate({
            ...decisionDraft,
            comment: decisionComment.trim(),
          });
        }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Typography.Text>
            {decisionDraft?.item.source_name}
          </Typography.Text>
          <Typography.Text type="secondary">
            {decisionDraft?.decision === 'accept_candidate'
              ? '确认候选正常拆板图、余量增长图及两份报告均已核对。'
              : '该决定会阻止项目进入 Excel 阶段，直到此条目改为采用候选。'}
          </Typography.Text>
          <Input.TextArea
            rows={3}
            maxLength={1000}
            showCount
            placeholder="填写核对结论或人工处理原因（至少 2 个字符）"
            value={decisionComment}
            onChange={(event) => setDecisionComment(event.target.value)}
          />
        </Space>
      </Modal>
    </Card>
  );
}
