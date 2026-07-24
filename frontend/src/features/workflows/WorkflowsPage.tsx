import { useMemo, useState } from 'react';
import {
  App,
  Button,
  Empty,
  Progress,
  Select,
  Space,
  Table,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  EyeOutlined,
  PlusOutlined,
  ProjectOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { parseApiError } from '../../shared/api';
import {
  fmtDateTime,
  PageHeader,
  StatCard,
  StatGrid,
  StatusChip,
} from '../../shared/components';
import { ProductionProjectCreateDrawer } from './ProductionProjectCreateDrawer';
import {
  createProductionProject,
  listWorkflows,
  listWorkflowTemplates,
} from './workflows.api';
import { WORKFLOW_STATUS } from './model/workflowPresentation';
import type { ProductionProjectCreatePayload } from './workflows.api';
import type { WorkflowRun, WorkflowTemplate } from './workflow';

export function WorkflowsPage() {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [codeError, setCodeError] = useState<string>();

  const workflowsQ = useQuery({
    queryKey: ['workflows', page, pageSize, status],
    queryFn: () => listWorkflows({
      page,
      page_size: pageSize,
      status,
      workflow_type: 'linux_production',
    }),
    refetchInterval: (query) => query.state.data?.data.some(
      (item) => item.status === 'running',
    ) ? 4000 : false,
  });
  const templatesQ = useQuery({
    queryKey: ['workflow-templates'],
    queryFn: listWorkflowTemplates,
  });
  const templateMap = useMemo(
    () => new Map<string, WorkflowTemplate>(
      (templatesQ.data ?? []).map((template) => [template.code, template]),
    ),
    [templatesQ.data],
  );
  const workflows = workflowsQ.data?.data ?? [];

  const closeCreate = () => {
    if (createM.isPending) return;
    setCreateOpen(false);
    setCodeError(undefined);
  };
  const createM = useMutation({
    mutationFn: (payload: ProductionProjectCreatePayload) => (
      createProductionProject(payload)
    ),
    onMutate: () => setCodeError(undefined),
    onSuccess: ({ workflow }) => {
      setCreateOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
      void queryClient.invalidateQueries({ queryKey: ['projects'] });
      message.success('生产项目与完整工作流已创建');
      navigate(`/workflows/${workflow.id}`);
    },
    onError: (error) => {
      const parsed = parseApiError(error, '生产项目创建失败');
      if (parsed.code === 'PROJECT_CODE_EXISTS') {
        setCodeError('该项目编号已存在，请更换编号');
      }
      if (
        parsed.code === 'PRODUCTION_WORKFLOW_ALREADY_EXISTS'
        && parsed.workflowId
      ) {
        modal.confirm({
          title: '该项目已有完整生产流程',
          content: '不能重复创建生产流程，是否进入现有项目工作流？',
          okText: '进入现有流程',
          cancelText: '留在当前页面',
          onOk: () => navigate(`/workflows/${parsed.workflowId}`),
        });
        return;
      }
      message.error(parsed.message);
    },
  });

  const summary = workflowsQ.data?.summary;

  const columns = [
    {
      title: '生产项目',
      dataIndex: 'project_id',
      minWidth: 280,
      render: (projectId: number, record: WorkflowRun) => {
        const templateName = templateMap.get(record.workflow_type)?.name
          ?? record.workflow_type;
        return (
          <div className="production-project-identity">
            <Typography.Text className="production-project-code" strong>
              {record.project_code ?? `#${projectId}`}
            </Typography.Text>
            <Typography.Text>{record.project_name ?? record.name}</Typography.Text>
            <small>Workflow #{record.id} · {templateName}</small>
          </div>
        );
      },
    },
    {
      title: '完整流程状态',
      dataIndex: 'status',
      width: 138,
      render: (value: string) => (
        <StatusChip style={WORKFLOW_STATUS[value] ?? WORKFLOW_STATUS.draft} />
      ),
    },
    {
      title: '当前生产阶段',
      dataIndex: 'current_stage',
      width: 230,
      render: (value: string | null, record: WorkflowRun) => {
        const stage = templateMap.get(record.workflow_type)?.stages.find(
          (item) => item.code === value,
        );
        return stage?.name ?? value ?? '尚未启动';
      },
    },
    {
      title: '总进度',
      dataIndex: 'progress',
      width: 190,
      render: (value: number) => <Progress percent={value} size="small" />,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: (value: string) => (
        <Typography.Text type="secondary">{fmtDateTime(value)}</Typography.Text>
      ),
    },
    {
      title: '操作',
      width: 116,
      align: 'right' as const,
      render: (_: unknown, record: WorkflowRun) => (
        <Button
          type="link"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/workflows/${record.id}`)}
        >
          进入项目
        </Button>
      ),
    },
  ];

  return (
    <div className="production-project-page">
      <PageHeader
        title="生产项目"
        subtitle="一个项目贯穿从资料入库到交付归档的完整工作流"
        extra={(
          <Space>
            <Button
              icon={<ReloadOutlined />}
              loading={workflowsQ.isFetching}
              onClick={() => workflowsQ.refetch()}
            >
              刷新
            </Button>
            <Button
              type="primary"
              size="large"
              icon={<PlusOutlined />}
              onClick={() => setCreateOpen(true)}
            >
              新建生产项目
            </Button>
          </Space>
        )}
      />

      <StatGrid>
        <StatCard
          label="项目总数"
          value={summary?.total ?? 0}
          icon={<ProjectOutlined />}
          color="#0f5d66"
          bg="#e9f8f7"
        />
        <StatCard
          label="进行中"
          value={summary?.running ?? 0}
          icon={<ReloadOutlined spin={(summary?.running ?? 0) > 0} />}
          color="#2563eb"
          bg="#eff6ff"
        />
        <StatCard
          label="待操作"
          value={summary?.waiting ?? 0}
          icon={<ClockCircleOutlined />}
          color="#b45309"
          bg="#fff8e8"
        />
        <StatCard
          label="已完成"
          value={summary?.completed ?? 0}
          icon={<CheckCircleOutlined />}
          color="#047857"
          bg="#ecfdf5"
        />
      </StatGrid>

      <div className="table-toolbar">
        <Select
          allowClear
          placeholder="筛选流程状态"
          value={status}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
          style={{ width: 190 }}
          options={Object.entries(WORKFLOW_STATUS).map(([value, meta]) => ({
            value,
            label: meta.label,
          }))}
        />
        <Typography.Text type="secondary">
          共 {workflowsQ.data?.pagination.total ?? 0} 个生产项目
        </Typography.Text>
      </div>

      <Table
        className="surface-table production-project-table"
        rowKey="id"
        dataSource={workflows}
        columns={columns}
        loading={workflowsQ.isLoading}
        scroll={{ x: 1050 }}
        onRow={(record) => ({
          onDoubleClick: () => navigate(`/workflows/${record.id}`),
        })}
        pagination={{
          current: page,
          pageSize,
          total: workflowsQ.data?.pagination.total ?? 0,
          showSizeChanger: true,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
          },
        }}
        locale={{
          emptyText: (
            <Empty description={status ? '当前筛选没有生产项目' : '还没有生产项目'}>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setCreateOpen(true)}
              >
                创建第一个生产项目
              </Button>
            </Empty>
          ),
        }}
      />

      <ProductionProjectCreateDrawer
        open={createOpen}
        pending={createM.isPending}
        codeError={codeError}
        onClose={closeCreate}
        onCodeChange={() => setCodeError(undefined)}
        onSubmit={(payload) => createM.mutate(payload)}
      />
    </div>
  );
}
