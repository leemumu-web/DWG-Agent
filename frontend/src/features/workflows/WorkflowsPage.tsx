import { useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Progress,
  Select,
  Space,
  Steps,
  Table,
  Typography,
} from 'antd';
import {
  ApartmentOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CloudUploadOutlined,
  ClockCircleOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { describeApiError } from '../../shared/api';
import {
  fmtDateTime,
  PageHeader,
  StatCard,
  StatGrid,
  StatusChip,
} from '../../shared/components';
import { listProjects } from '../projects';
import {
  createWorkflow,
  listWorkflows,
  listWorkflowTemplates,
  startWorkflow,
} from './workflows.api';
import {
  suggestedBatchName,
  WORKFLOW_STATUS,
} from './model/workflowPresentation';
import type { WorkflowRun, WorkflowTemplate } from './workflow';

export function WorkflowsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [batchNameTouched, setBatchNameTouched] = useState(false);
  const [form] = Form.useForm();

  const workflowsQ = useQuery({
    queryKey: ['workflows', page, pageSize, status],
    queryFn: () => listWorkflows({ page, page_size: pageSize, status }),
    refetchInterval: (query) => query.state.data?.data.some((item) => item.status === 'running')
      ? 4000
      : false,
  });
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const templatesQ = useQuery({
    queryKey: ['workflow-templates'],
    queryFn: listWorkflowTemplates,
  });
  const projectMap = useMemo(
    () => new Map((projectsQ.data ?? []).map((project) => [project.id, project])),
    [projectsQ.data],
  );
  const templateMap = useMemo(
    () => new Map<string, WorkflowTemplate>(
      (templatesQ.data ?? []).map((template) => [template.code, template]),
    ),
    [templatesQ.data],
  );
  const workflows = workflowsQ.data?.data ?? [];
  const hasProjects = (projectsQ.data?.length ?? 0) > 0;

  const closeCreate = () => {
    setCreateOpen(false);
    setBatchNameTouched(false);
    form.resetFields();
  };
  const createM = useMutation({
    mutationFn: async (values: { project_id: number; name: string }) => {
      const created = await createWorkflow({ ...values, workflow_type: 'linux_production' });
      try {
        return { workflow: await startWorkflow(created.id), startError: null as unknown };
      } catch (startError) {
        return { workflow: created, startError };
      }
    },
    onSuccess: ({ workflow, startError }) => {
      closeCreate();
      void queryClient.invalidateQueries({ queryKey: ['workflows'] });
      if (startError) {
        message.warning(`批次已创建但启动失败：${describeApiError(startError, '请在详情页重试启动')}`);
      } else {
        message.success('生产批次已创建并启动，请提交生产资料');
      }
      navigate(`/workflows/${workflow.id}`);
    },
    onError: (error) => message.error(describeApiError(error, '生产批次创建失败')),
  });

  const selectProject = (projectId: number | undefined) => {
    const project = projectId ? projectMap.get(projectId) : undefined;
    if (!batchNameTouched) {
      form.setFieldValue('name', project ? suggestedBatchName(project.code) : undefined);
    }
  };
  const runningCount = workflows.filter((item) => item.status === 'running').length;
  const waitingCount = workflows.filter((item) => ['waiting_input', 'waiting_review'].includes(item.status)).length;
  const completedCount = workflows.filter((item) => item.status === 'succeeded').length;

  const columns = [
    {
      title: '生产批次',
      dataIndex: 'name',
      render: (name: string, record: WorkflowRun) => (
        <div>
          <Typography.Text strong>{name}</Typography.Text>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              #{record.id} · {templateMap.get(record.workflow_type)?.name ?? record.workflow_type}
            </Typography.Text>
          </div>
        </div>
      ),
    },
    {
      title: '项目',
      dataIndex: 'project_id',
      width: 180,
      render: (id: number) => projectMap.get(id)?.name ?? `#${id}`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (value: string) => (
        <StatusChip style={WORKFLOW_STATUS[value] ?? WORKFLOW_STATUS.draft} />
      ),
    },
    {
      title: '当前阶段',
      dataIndex: 'current_stage',
      width: 210,
      render: (value: string | null, record: WorkflowRun) => {
        const stage = templateMap.get(record.workflow_type)?.stages.find((item) => item.code === value);
        return stage?.name ?? value ?? '尚未启动';
      },
    },
    {
      title: '进度',
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
      width: 100,
      align: 'right' as const,
      render: (_: unknown, record: WorkflowRun) => (
        <Button
          type="link"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/workflows/${record.id}`)}
        >
          打开
        </Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="生产流程"
        subtitle="建立生产批次，冻结多个 DWG 与唯一 Tekla Excel，并按阶段推进到正式交付"
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
              新建生产批次
            </Button>
          </Space>
        )}
      />
      <StatGrid>
        <StatCard label="当前页批次" value={workflows.length} icon={<ApartmentOutlined />} color="#155e75" bg="#ecfeff" />
        <StatCard label="进行中" value={runningCount} icon={<ReloadOutlined spin={runningCount > 0} />} color="#2563eb" bg="#eff6ff" />
        <StatCard label="待操作" value={waitingCount} icon={<ClockCircleOutlined />} color="#d97706" bg="#fffbeb" />
        <StatCard label="已完成" value={completedCount} icon={<CheckCircleOutlined />} color="#059669" bg="#ecfdf5" />
      </StatGrid>
      <div className="table-toolbar">
        <Select
          allowClear
          placeholder="筛选状态"
          value={status}
          onChange={(value) => { setStatus(value); setPage(1); }}
          style={{ width: 180 }}
          options={Object.entries(WORKFLOW_STATUS).map(([value, meta]) => ({
            value,
            label: meta.label,
          }))}
        />
        <Typography.Text type="secondary">
          共 {workflowsQ.data?.pagination.total ?? 0} 个生产批次
        </Typography.Text>
      </div>
      <Table
        className="surface-table"
        rowKey="id"
        dataSource={workflows}
        columns={columns}
        loading={workflowsQ.isLoading}
        scroll={{ x: 1100 }}
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
            <Empty description="还没有生产批次">
              <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setCreateOpen(true)}>
                新建第一批资料
              </Button>
            </Empty>
          ),
        }}
      />

      <Drawer
        title="新建生产批次"
        open={createOpen}
        onClose={closeCreate}
        width={540}
        closable={!createM.isPending}
        maskClosable={!createM.isPending}
      >
        <Form
          className="production-create-form"
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => createM.mutate(values)}
        >
          <section className="production-create-hero" aria-label="生产批次说明">
            <Typography.Text className="production-create-eyebrow">生产资料入口</Typography.Text>
            <Typography.Title level={4}>先建批次，再上传并冻结资料</Typography.Title>
            <Typography.Paragraph>
              上传多个 DWG 与恰好一个 Tekla Excel；DXF 由服务器生成，不需要人工准备。
            </Typography.Paragraph>
          </section>
          <Steps
            className="production-create-steps"
            size="small"
            current={0}
            responsive
            items={[
              { title: '选择项目' },
              { title: '确认批次名' },
              { title: '进入批次详情' },
            ]}
          />
          <div className="production-create-checklist" aria-label="文件准备清单">
            <span><CheckCircleOutlined /> 多个 DWG + 1 个 Excel</span>
            <span><CloudServerOutlined /> 无需准备 DXF</span>
          </div>
          <Form.Item
            name="project_id"
            label="所属项目"
            rules={[{ required: true, message: '请选择项目' }]}
            extra="批次继承项目权限和文件归属。"
          >
            <Select
              showSearch
              optionFilterProp="label"
              disabled={createM.isPending}
              placeholder="选择生产资料所属项目"
              onChange={selectProject}
              options={(projectsQ.data ?? []).map((project) => ({
                value: project.id,
                label: `${project.code} · ${project.name}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="name"
            label="批次名称"
            rules={[
              { required: true, message: '请输入批次名称' },
              { max: 128, message: '批次名称不能超过 128 个字符' },
            ]}
            extra="已按项目和日期生成建议名称，可按现场规则修改。"
          >
            <Input
              disabled={createM.isPending}
              placeholder="例如 P001-20260724-生产批次"
              onChange={() => setBatchNameTouched(true)}
            />
          </Form.Item>
          {hasProjects ? (
            <div className="production-create-actions">
              <Typography.Text type="secondary">
                创建并启动后将进入独立详情页继续上传。
              </Typography.Text>
              <Space className="production-create-action-buttons" wrap>
                <Button disabled={createM.isPending} onClick={closeCreate}>取消</Button>
                <Button
                  htmlType="submit"
                  type="primary"
                  icon={<CloudUploadOutlined />}
                  loading={createM.isPending}
                >
                  创建并进入资料上传
                </Button>
              </Space>
            </div>
          ) : (
            <Alert
              type="warning"
              showIcon
              message="需要先创建项目"
              description="生产批次必须归属一个项目，当前没有可选项目。"
              action={<Button onClick={() => { setCreateOpen(false); message.info('项目创建已迁移到工作流系统，请联系管理员通过 API 创建项目。'); }}>了解详情</Button>}
            />
          )}
        </Form>
      </Drawer>
    </>
  );
}
