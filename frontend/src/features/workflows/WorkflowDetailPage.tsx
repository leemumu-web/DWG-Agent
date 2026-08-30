import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Popconfirm,
  Progress,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  ArrowLeftOutlined,
  ClockCircleOutlined,
  FileExcelOutlined,
  ReloadOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, useParams } from 'react-router-dom';

import {
  describeApiError,
  operatorErrorMessage,
  parseApiError,
  type ParsedApiError,
} from '../../shared/api';
import { useAuthStore } from '../../shared/auth';
import {
  ApiErrorAlert,
  ExcelInputFailurePanel,
  fmtDateTime,
  StatusChip,
} from '../../shared/components';
import { listProjects } from '../projects';
import { DxfClassificationPanel } from './DxfClassificationPanel';
import { DrawingProcessingPanel } from './DrawingProcessingPanel';
import { ExcelStage2Panel } from './ExcelStage2Panel';
import { ExcelStage3Panel } from './ExcelStage3Panel';
import { FutureStageNotice } from './FutureStageNotice';
import { PlXboxDrawingProcessingPanel } from './PlXboxDrawingProcessingPanel';
import { ProductionInputPanel } from './ProductionInputPanel';
import { WorkflowArtifactSummary } from './WorkflowArtifactSummary';
import { WorkflowRetentionControl } from './WorkflowRetentionControl';
import { WorkflowStageArchiveCard } from './WorkflowStageArchiveCard';
import { stageStateLabel, WorkflowStageRail } from './WorkflowStageRail';
import {
  cancelWorkflow,
  completeWorkflowStage,
  executeWorkflowStage,
  getWorkflowExcelStagePreflight,
  getWorkflow,
  listWorkflowTemplates,
  startWorkflow,
} from './workflows.api';
import {
  ACTIONABLE,
  capabilityTag,
  isWaitingLaunchStage,
  STAGE_STATUS,
  TERMINAL,
  WORKFLOW_STATUS,
} from './model/workflowPresentation';
import type { WorkflowArtifact, WorkflowStage } from './workflow';

export function WorkflowDetailPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((state) => state.user);
  const params = useParams();
  const workflowId = Number(params.workflowId);
  const [executionError, setExecutionError] = useState<ParsedApiError | null>(null);
  const [selectedStageCode, setSelectedStageCode] = useState<string | null>(null);

  const detailQ = useQuery({
    queryKey: ['workflow', workflowId],
    queryFn: () => getWorkflow(workflowId),
    enabled: Number.isInteger(workflowId) && workflowId > 0,
    refetchInterval: (query) => ['running'].includes(query.state.data?.status ?? '') ? 4000 : false,
  });
  const templatesQ = useQuery({
    queryKey: ['workflow-templates'],
    queryFn: listWorkflowTemplates,
  });
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const detail = detailQ.data;
  const isAdmin = currentUser?.roles.some(
    (role) => ['admin', 'super_admin'].includes(role.code),
  ) ?? false;
  const template = templatesQ.data?.find((item) => item.code === detail?.workflow_type);
  const capabilities = useMemo(
    () => new Map((template?.stages ?? []).map((stage) => [stage.code, stage])),
    [template],
  );
  const authoritativeCurrentStage = detail?.stages.find(
    (stage) => stage.stage_code === detail.current_stage,
  )
    ?? detail?.stages.find((stage) => ACTIONABLE.has(stage.status));
  const authoritativeCurrentCapability = authoritativeCurrentStage
    ? capabilities.get(authoritativeCurrentStage.stage_code)
    : undefined;
  const selectedStage = detail?.stages.find(
    (stage) => stage.stage_code === selectedStageCode,
  ) ?? authoritativeCurrentStage;
  const selectedCapability = selectedStage
    ? capabilities.get(selectedStage.stage_code)
    : undefined;
  const selectedIsCurrent = Boolean(
    selectedStage
    && authoritativeCurrentStage
    && selectedStage.stage_code === authoritativeCurrentStage.stage_code,
  );
  const stageById = new Map((detail?.stages ?? []).map((stage) => [stage.id, stage]));
  // Attempt 世代过滤：拆板（drawing_processing）与 PL/XBOX 拆板
  // （pl_xbox_split）阶段的产物必须匹配当前 stage 的 job_id 与
  // job_attempt，旧世代产物被隐藏以免与正式结果混淆；其他阶段不过滤
  // （产物不按世代区分）。
  const visibleArtifacts = (detail?.artifacts ?? []).filter((artifact) => {
    const artifactStage = typeof artifact.stage_run_id === 'number'
      ? stageById.get(artifact.stage_run_id)
      : undefined;
    if (!artifactStage) return true;
    if (!['drawing_processing', 'pl_xbox_split'].includes(artifactStage.stage_code)) return true;
    if (!['succeeded', 'waiting_review'].includes(artifactStage.status)) return false;
    return artifact.metadata_json?.job_id === artifactStage.job_id
      && artifact.metadata_json?.job_attempt === artifactStage.job_attempt;
  }) ?? [];
  const selectedArtifacts = visibleArtifacts.filter(
    (artifact) => artifact.stage_run_id === selectedStage?.id,
  );
  const project = projectsQ.data?.find((item) => item.id === detail?.project_id);
  const sourceExcel = detail?.artifacts.find((item) => item.artifact_type === 'source_excel');
  const excelPreflightEnabled = Boolean(
    selectedStage
    && selectedIsCurrent
    && selectedStage.stage_code === 'excel_stage1',
  );
  const excelPreflightQ = useQuery({
    queryKey: ['workflow-excel-stage1-preflight', workflowId],
    queryFn: () => getWorkflowExcelStagePreflight(workflowId),
    enabled: excelPreflightEnabled,
    retry: false,
  });
  const excelPreflightError = excelPreflightQ.isError
    ? parseApiError(excelPreflightQ.error, 'Excel 执行前检查失败')
    : null;

  useEffect(() => {
    if (
      selectedStageCode
      && detail
      && !detail.stages.some((stage) => stage.stage_code === selectedStageCode)
    ) {
      setSelectedStageCode(null);
    }
  }, [detail, selectedStageCode]);

  const refresh = () => {
    setExecutionError(null);
    void queryClient.invalidateQueries({ queryKey: ['workflow', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['workflows'] });
  };
  const startM = useMutation({
    mutationFn: () => startWorkflow(workflowId),
    onSuccess: () => { message.success('流程已启动'); refresh(); },
    onError: (error) => message.error(describeApiError(error, '启动失败')),
  });
  const cancelM = useMutation({
    mutationFn: () => cancelWorkflow(workflowId),
    onSuccess: () => { message.success('流程及当前任务已取消'); refresh(); },
    onError: (error) => message.error(describeApiError(error, '取消失败')),
  });
  const completeM = useMutation({
    mutationFn: () => {
      if (!authoritativeCurrentStage || !selectedIsCurrent) {
        throw new Error('当前查看阶段不可确认');
      }
      return completeWorkflowStage(workflowId, authoritativeCurrentStage.stage_code);
    },
    onSuccess: () => {
      setSelectedStageCode(authoritativeCurrentStage?.stage_code ?? null);
      message.success('当前阶段已确认');
      refresh();
    },
    onError: (error) => message.error(describeApiError(error, '确认失败')),
  });
  const executeM = useMutation({
    mutationFn: () => {
      if (
        !authoritativeCurrentStage
        || !authoritativeCurrentCapability?.execution_kind
        || !selectedIsCurrent
      ) {
        throw new Error('当前阶段没有服务器执行接口');
      }
      return executeWorkflowStage(workflowId, authoritativeCurrentStage.stage_code, {
        execution_kind: authoritativeCurrentCapability.execution_kind,
      });
    },
    onMutate: () => setExecutionError(null),
    onSuccess: (result) => {
      setSelectedStageCode(authoritativeCurrentStage?.stage_code ?? null);
      queryClient.setQueryData(['workflow', workflowId], result.workflow);
      message.success(
        result.retried
          ? `任务 #${result.job.id} 已重新入队`
          : result.reused
            ? `继续跟踪任务 #${result.job.id}`
            : `任务 #${result.job.id} 已提交`,
      );
      refresh();
    },
    onError: (error) => {
      const parsed = parseApiError(error, '阶段执行失败');
      setExecutionError(parsed);
      if (!parsed.failure) message.error(parsed.message);
    },
  });

  if (!Number.isInteger(workflowId) || workflowId <= 0) {
    return <Navigate to="/workflows" replace />;
  }
  if (detailQ.isLoading || templatesQ.isLoading) {
    return <div className="workflow-detail-loading"><Spin size="large" /></div>;
  }
  if (detailQ.isError || !detail) {
    return (
      <ApiErrorAlert
        title="生产批次加载失败"
        error={detailQ.error}
        fallback="请确认批次存在且当前账号有权访问"
        retryLoading={detailQ.isFetching}
        onRetry={() => detailQ.refetch()}
        extraAction={<Button href="/workflows">返回列表</Button>}
      />
    );
  }

  const manualConfirmation = Boolean(
    selectedStage
    && selectedCapability
    && selectedIsCurrent
    && selectedStage.stage_code !== 'source_intake'
    && !isWaitingLaunchStage(selectedStage.stage_code)
    && selectedCapability.execution_mode === 'manual',
  );
  const selectedIsPast = Boolean(
    selectedStage
    && (
      ['succeeded', 'skipped'].includes(selectedStage.status)
      || (
        authoritativeCurrentStage
        && selectedStage.sequence < authoritativeCurrentStage.sequence
      )
    ),
  );

  return (
    <div className="workflow-detail-page">
      <header className="workflow-detail-header">
        <div>
          <Link to="/workflows" className="workflow-detail-back">
            <ArrowLeftOutlined /> 返回生产批次
          </Link>
          <Typography.Title level={2}>生产批次 #{detail.id}</Typography.Title>
          <div className="workflow-detail-name">{detail.name}</div>
          <Space wrap>
            <StatusChip style={WORKFLOW_STATUS[detail.status] ?? WORKFLOW_STATUS.draft} />
            <Typography.Text type="secondary">
              {project ? `${project.code} · ${project.name}` : `项目 #${detail.project_id}`}
            </Typography.Text>
            <Typography.Text type="secondary">更新于 {fmtDateTime(detail.updated_at)}</Typography.Text>
          </Space>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={detailQ.isFetching} onClick={() => detailQ.refetch()}>
            刷新
          </Button>
          {TERMINAL.has(detail.status) && (
            <WorkflowRetentionControl
              workflowId={detail.id}
              isAdmin={isAdmin}
              onPurged={refresh}
            />
          )}
          {detail.status === 'draft' && (
            <Button type="primary" loading={startM.isPending} onClick={() => startM.mutate()}>
              启动流程
            </Button>
          )}
          {!TERMINAL.has(detail.status) && (
            <Popconfirm title="确定取消流程及当前活动任务？" onConfirm={() => cancelM.mutate()}>
              <Button danger icon={<StopOutlined />} loading={cancelM.isPending}>取消批次</Button>
            </Popconfirm>
          )}
        </Space>
      </header>

      <div className="workflow-detail-progress">
        <span>整体进度</span>
        <Progress percent={detail.progress} />
      </div>

      {detail.workflow_type === 'linux_production' && (
        <Alert
          type="info"
          showIcon
          message="图纸主格式：DXF"
          description="DWG 只在输入阶段留档；服务器转换并冻结后，后续图纸产物必须是 DXF。Excel、报告和清单保持各自格式。"
        />
      )}

      <div className="workflow-detail-grid">
        <WorkflowStageRail
          stages={detail.stages}
          capabilities={capabilities}
          currentCode={detail.current_stage}
          selectedCode={selectedStage?.stage_code}
          onSelect={(stageCode) => {
            setExecutionError(null);
            setSelectedStageCode(
              stageCode === authoritativeCurrentStage?.stage_code ? null : stageCode,
            );
          }}
        />
        <main className="workflow-stage-workspace">
          {selectedStage && selectedCapability ? (
            <>
              <section className="workflow-stage-heading">
                <div>
                  <span>STAGE {String(selectedStage.sequence).padStart(2, '0')}</span>
                  <Typography.Title level={3}>{selectedStage.name}</Typography.Title>
                  <Typography.Paragraph>{selectedCapability.description}</Typography.Paragraph>
                </div>
                <Space wrap>
                  {capabilityTag(selectedCapability, selectedStage.stage_code)}
                  <Tag>{stageStateLabel(selectedStage)}</Tag>
                </Space>
              </section>

              {!selectedIsCurrent && (
                <Alert
                  type={selectedIsPast ? 'info' : 'warning'}
                  showIcon
                  message={selectedIsPast ? '正在查看历史阶段' : '该阶段尚未解锁'}
                  description={selectedIsPast
                    ? '历史阶段仅供核对结果和下载已有压缩包，不能重新上传、执行或确认。'
                    : '可以提前查看阶段要求；上传、执行和确认仍严格绑定服务器当前阶段。'}
                  action={authoritativeCurrentStage && (
                    <Button onClick={() => setSelectedStageCode(null)}>
                      返回当前阶段
                    </Button>
                  )}
                />
              )}

              {selectedStage.error_message && (
                <Alert
                  type="error"
                  showIcon
                  message="阶段处理未完成"
                  description={operatorErrorMessage(
                    selectedStage.error_code,
                    selectedStage.error_message,
                    '当前阶段未能完成，请刷新状态并按本阶段提示处理。',
                  )}
                />
              )}
              {selectedIsCurrent && executionError?.failure && (
                <ExcelInputFailurePanel
                  failure={executionError.failure}
                  requestId={executionError.requestId}
                />
              )}

              {!['dxf_classification', 'drawing_processing', 'pl_xbox_split'].includes(
                selectedStage.stage_code,
              ) && !isWaitingLaunchStage(selectedStage.stage_code) && (
                <WorkflowStageArchiveCard
                  workflowId={detail.id}
                  stage={selectedStage}
                  artifacts={selectedArtifacts}
                />
              )}

              {selectedStage.stage_code === 'source_intake' && (
                <ProductionInputPanel
                  workflowId={detail.id}
                  sourceIntakeActive={selectedIsCurrent}
                  onFrozen={() => {
                    setSelectedStageCode(selectedStage.stage_code);
                    refresh();
                  }}
                />
              )}
              {selectedStage.stage_code === 'dxf_classification' && (
                <DxfClassificationPanel
                  workflowId={detail.id}
                  stage={selectedStage}
                  isCurrent={selectedIsCurrent}
                  onChanged={() => {
                    setSelectedStageCode(selectedStage.stage_code);
                    refresh();
                  }}
                />
              )}
              {selectedStage.stage_code === 'drawing_processing' && (
                <DrawingProcessingPanel
                  workflowId={detail.id}
                  stage={selectedStage}
                  isCurrent={selectedIsCurrent}
                  onChanged={() => {
                    setSelectedStageCode(selectedStage.stage_code);
                    refresh();
                  }}
                />
              )}
              {selectedStage.stage_code === 'pl_xbox_split' && (
                <PlXboxDrawingProcessingPanel
                  workflowId={detail.id}
                  stage={selectedStage}
                  isCurrent={selectedIsCurrent}
                  onChanged={() => {
                    setSelectedStageCode(selectedStage.stage_code);
                    refresh();
                  }}
                />
              )}
              {isWaitingLaunchStage(selectedStage.stage_code) && (
                <FutureStageNotice />
              )}
              {selectedStage.stage_code === 'excel_stage1' && (
                <Card className="workflow-excel-stage-card">
                  <div className="workflow-excel-stage-source">
                    <span className="workflow-excel-stage-icon"><FileExcelOutlined /></span>
                    <div>
                      <Typography.Text strong>使用冻结输入中的 Excel</Typography.Text>
                      <p>
                        {typeof sourceExcel?.metadata_json?.original_name === 'string'
                          ? sourceExcel.metadata_json.original_name
                          : sourceExcel?.file_id
                            ? `已冻结文件 #${sourceExcel.file_id}`
                            : '系统将在执行前再次核对冻结清单、文件摘要和表格结构'}
                      </p>
                    </div>
                  </div>
                  <Descriptions
                    size="small"
                    column={1}
                    items={[
                      { key: 'input', label: '输入依据', children: '冻结输入清单中的唯一 source_excel，不允许临时替换' },
                      { key: 'output', label: '正式产物', children: '整理表与 part 表合并在 Excel 第一阶段结果中' },
                      { key: 'check', label: '执行前核验', children: '对象 SHA-256、Excel 格式、单工作表、标题行和必需列' },
                    ]}
                  />
                  {selectedIsCurrent && excelPreflightQ.isLoading && (
                    <Alert
                      type="info"
                      showIcon
                      message="正在执行运行前检查"
                      description="正在核对冻结清单、源表对象、Excel 结构与正式拆板交接。"
                    />
                  )}
                  {selectedIsCurrent && excelPreflightQ.data?.ready && (
                    <Alert
                      type="success"
                      showIcon
                      message="运行前检查通过"
                      description={(
                        <Space orientation="vertical" size={4}>
                          <Typography.Text>
                            源表：{excelPreflightQ.data.source_file_name}
                          </Typography.Text>
                          <Typography.Text>
                            {excelPreflightQ.data.official_pair_count === 0
                              ? '无需拆板：本批没有可拆板图纸'
                              : `正式拆板：${excelPreflightQ.data.official_pair_count} 对图纸`}
                          </Typography.Text>
                          <Space wrap size={[4, 4]}>
                            {excelPreflightQ.data.checks.map((check) => (
                              <Tag color="green" key={check.code}>{check.label}</Tag>
                            ))}
                          </Space>
                        </Space>
                      )}
                    />
                  )}
                  {selectedIsCurrent && excelPreflightError?.failure && (
                    <ExcelInputFailurePanel
                      failure={excelPreflightError.failure}
                      requestId={excelPreflightError.requestId}
                    />
                  )}
                  {selectedIsCurrent && excelPreflightError && !excelPreflightError.failure && (
                    <ApiErrorAlert
                      title="Excel 第一阶段运行前检查未通过"
                      error={excelPreflightQ.error}
                      fallback="Excel 运行前检查未通过"
                      retryLabel="重新检查"
                      retryLoading={excelPreflightQ.isFetching}
                      onRetry={() => excelPreflightQ.refetch()}
                    />
                  )}
                  {selectedIsCurrent && (
                    <Button
                      type="primary"
                      size="large"
                      icon={<ThunderboltOutlined />}
                      loading={executeM.isPending || excelPreflightQ.isLoading}
                      disabled={!excelPreflightQ.data?.ready}
                      onClick={() => executeM.mutate()}
                    >
                      运行 Excel 第一阶段
                    </Button>
                  )}
                </Card>
              )}
              {selectedStage.stage_code === 'excel_stage2' && (
                <ExcelStage2Panel
                  workflowId={detail.id}
                  stage={selectedStage}
                  isCurrent={selectedIsCurrent}
                  executing={executeM.isPending}
                  onExecute={() => executeM.mutate()}
                />
              )}
              {selectedStage.stage_code === 'excel_stage3' && (
                <ExcelStage3Panel
                  workflowId={detail.id}
                  stage={selectedStage}
                  isCurrent={selectedIsCurrent}
                  executing={executeM.isPending}
                  onExecute={() => executeM.mutate()}
                />
              )}
              {manualConfirmation && (
                <Card className="workflow-manual-stage">
                  <Space orientation="vertical">
                    <Typography.Text strong>本阶段需要人工确认</Typography.Text>
                    <Typography.Text type="secondary">请先核对阶段要求与已有产物，再确认进入下一阶段。</Typography.Text>
                    <Button type="primary" loading={completeM.isPending} onClick={() => completeM.mutate()}>
                      确认当前阶段
                    </Button>
                  </Space>
                </Card>
              )}
              {selectedStage.job_id && !isWaitingLaunchStage(selectedStage.stage_code) && (
                <Card size="small" className="workflow-current-job">
                  <Space>
                    <ClockCircleOutlined />
                    <Typography.Text>任务 #{selectedStage.job_id} · 尝试 #{selectedStage.job_attempt}</Typography.Text>
                    <Link to="/jobs">查看任务记录</Link>
                  </Space>
                  <Progress
                    percent={selectedStage.progress}
                    status={STAGE_STATUS[selectedStage.status] === 'error' ? 'exception' : 'active'}
                  />
                </Card>
              )}
            </>
          ) : (
            <Empty description="当前没有待处理阶段" />
          )}
          <WorkflowArtifactSummary workflowId={detail.id} artifacts={visibleArtifacts} />
        </main>
      </div>
    </div>
  );
}
