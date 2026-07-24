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
  DownloadOutlined,
  FileExcelOutlined,
  LockOutlined,
  ReloadOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, useParams } from 'react-router-dom';

import { describeApiError, parseApiError, type ParsedApiError } from '../../shared/api';
import {
  ExcelInputFailurePanel,
  fmtDateTime,
  StatusChip,
} from '../../shared/components';
import { listProjects } from '../projects';
import { DxfClassificationPanel } from './DxfClassificationPanel';
import { ProductionInputPanel } from './ProductionInputPanel';
import { stageStateLabel, WorkflowStageRail } from './WorkflowStageRail';
import {
  cancelWorkflow,
  completeWorkflowStage,
  downloadWorkflowArchive,
  downloadWorkflowStageArchive,
  executeWorkflowStage,
  getWorkflow,
  listWorkflowTemplates,
  startWorkflow,
} from './workflows.api';
import {
  ACTIONABLE,
  capabilityTag,
  STAGE_STATUS,
  TERMINAL,
  WORKFLOW_STATUS,
} from './model/workflowPresentation';
import type {
  WorkflowArtifact,
  WorkflowStage,
  WorkflowStageCapability,
} from './workflow';

function ArtifactLedger({ workflowId, artifacts }: { workflowId: number; artifacts: WorkflowArtifact[] }) {
  const { message } = App.useApp();
  const archiveM = useMutation({
    mutationFn: () => downloadWorkflowArchive(workflowId),
    onError: (error) => message.error(describeApiError(error, '生产压缩包下载失败')),
  });
  return (
    <Card
      className="workflow-artifact-ledger"
      title="生产产物与证据"
      extra={<Button icon={<DownloadOutlined />} loading={archiveM.isPending} disabled={!artifacts.length} onClick={() => archiveM.mutate()}>下载完整生产压缩包</Button>}
    >
      {artifacts.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前尚无已登记产物" />
      ) : (
        <div className="workflow-artifact-list">
          {artifacts.map((artifact) => (
            <div key={artifact.id} className="workflow-artifact-item">
              <div>
                <Tag>{artifact.artifact_type}</Tag>
                <Typography.Text strong>版本 v{artifact.version}</Typography.Text>
                <small>已登记 · {fmtDateTime(artifact.created_at)}</small>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function StageArchiveCard({
  workflowId,
  stage,
  capability,
  artifacts,
}: {
  workflowId: number;
  stage: WorkflowStage;
  capability: WorkflowStageCapability;
  artifacts: WorkflowArtifact[];
}) {
  const { message } = App.useApp();
  const archiveM = useMutation({
    mutationFn: () => downloadWorkflowStageArchive(workflowId, stage.stage_code),
    onError: (error) => message.error(describeApiError(error, '阶段结果压缩包下载失败')),
  });
  if (capability.implementation_status !== 'implemented') return null;
  const downloadLabel = stage.stage_code === 'dxf_classification'
    ? '下载分流结果压缩包'
    : '下载本阶段结果压缩包';
  return (
    <Card className="workflow-stage-archive-card">
      <div>
        <span>STAGE OUTPUT</span>
        <Typography.Text strong>
          {artifacts.length ? `已登记 ${artifacts.length} 项阶段产物` : '本阶段尚无可下载产物'}
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
    </Card>
  );
}

function DrawingProcessingPlaceholder() {
  const metrics = [
    ['项目总进度', '未接入'],
    ['已完成 / 总数', '未接入'],
    ['实时速度', '未接入'],
    ['预计剩余时间', '未接入'],
  ];
  return (
    <Card className="workflow-drawing-placeholder">
      <Alert
        type="warning"
        showIcon
        icon={<LockOutlined />}
        message="拆板执行能力尚未接入"
        description="第三步继续保持真实占位；下列指标是后续服务端执行器必须提供的数据合同，当前不显示模拟值。"
      />
      <div className="workflow-placeholder-metrics">
        {metrics.map(([label, value]) => (
          <div key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function WorkflowDetailPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const params = useParams();
  const workflowId = Number(params.workflowId);
  const [executionError, setExecutionError] = useState<ParsedApiError | null>(null);
  const [selectedStageCode, setSelectedStageCode] = useState<string | null>(null);

  const detailQ = useQuery({
    queryKey: ['workflow', workflowId],
    queryFn: () => getWorkflow(workflowId),
    enabled: Number.isInteger(workflowId) && workflowId > 0,
    refetchInterval: (query) => ['running'].includes(query.state.data?.status ?? '') ? 2500 : false,
  });
  const templatesQ = useQuery({
    queryKey: ['workflow-templates'],
    queryFn: listWorkflowTemplates,
  });
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const detail = detailQ.data;
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
  const selectedArtifacts = detail?.artifacts.filter(
    (artifact) => artifact.stage_run_id === selectedStage?.id,
  ) ?? [];
  const project = projectsQ.data?.find((item) => item.id === detail?.project_id);
  const sourceExcel = detail?.artifacts.find((item) => item.artifact_type === 'source_excel');

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
      setSelectedStageCode(null);
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
      setSelectedStageCode(null);
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
      <Alert
        type="error"
        showIcon
        message="生产批次加载失败"
        description={describeApiError(detailQ.error, '请确认批次存在且当前账号有权访问')}
        action={<Space><Button onClick={() => detailQ.refetch()}>重试</Button><Button href="/workflows">返回列表</Button></Space>}
      />
    );
  }

  const manualConfirmation = Boolean(
    selectedStage
    && selectedCapability
    && selectedIsCurrent
    && selectedStage.stage_code !== 'source_intake'
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
                  {capabilityTag(selectedCapability)}
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
                    : '可以提前查看阶段合同；上传、执行和确认仍严格绑定服务器当前阶段。'}
                  action={authoritativeCurrentStage && (
                    <Button onClick={() => setSelectedStageCode(null)}>
                      返回当前阶段
                    </Button>
                  )}
                />
              )}

              <Alert
                type="info"
                showIcon
                message="阶段数据合同"
                description={[
                  `所需输入：${selectedCapability.required_inputs.join('、') || '无'}`,
                  `允许产物：${selectedCapability.artifact_types.join('、') || '无'}`,
                  `完成必需产物：${selectedCapability.required_outputs.join('、') || '无'}`,
                ].join('；')}
              />

              {selectedStage.error_message && (
                <Alert
                  type="error"
                  showIcon
                  message={selectedStage.error_code ?? '阶段执行失败'}
                  description={selectedStage.error_message}
                />
              )}
              {selectedIsCurrent && executionError?.failure && (
                <ExcelInputFailurePanel
                  failure={executionError.failure}
                  requestId={executionError.requestId}
                />
              )}

              {selectedStage.stage_code !== 'dxf_classification' && (
                <StageArchiveCard
                  workflowId={detail.id}
                  stage={selectedStage}
                  capability={selectedCapability}
                  artifacts={selectedArtifacts}
                />
              )}

              {selectedStage.stage_code === 'source_intake' && (
                <ProductionInputPanel
                  workflowId={detail.id}
                  sourceIntakeActive={selectedIsCurrent}
                  onFrozen={() => {
                    setSelectedStageCode(null);
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
                    setSelectedStageCode(null);
                    refresh();
                  }}
                />
              )}
              {selectedStage.stage_code === 'drawing_processing' && (
                <DrawingProcessingPlaceholder />
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
                  {selectedIsCurrent && (
                    <Button
                      type="primary"
                      size="large"
                      icon={<ThunderboltOutlined />}
                      loading={executeM.isPending}
                      onClick={() => executeM.mutate()}
                    >
                      运行 Excel 第一阶段
                    </Button>
                  )}
                </Card>
              )}
              {selectedCapability.implementation_status !== 'implemented'
                && selectedStage.stage_code !== 'drawing_processing' && (
                <Alert
                  type="warning"
                  showIcon
                  message={selectedCapability.implementation_status === 'external'
                    ? '该阶段由外部生产节点完成'
                    : '该阶段接口已定义，执行能力尚未实现'}
                  description="当前不会提交虚假任务；请按阶段数据合同绑定真实产物后人工确认。"
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
              {selectedStage.job_id && (
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
          <ArtifactLedger workflowId={detail.id} artifacts={detail.artifacts} />
        </main>
      </div>
    </div>
  );
}
