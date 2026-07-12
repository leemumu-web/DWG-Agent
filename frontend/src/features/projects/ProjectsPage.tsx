import { useMemo, useState } from 'react';
import {
  App,
  Button,
  Descriptions,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  PlusOutlined,
  ReloadOutlined,
  ProjectOutlined,
  TeamOutlined,
  EyeOutlined,
  DeleteOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addProjectMember,
  createProject,
  deleteProject,
  listProjectMembers,
  listProjects,
  removeProjectMember,
  type ProjectMember,
} from '../../api/projects.api';
import { listUsers } from '../../api/users.api';
import type { Project } from '../../types/project';
import {
  fmtDateTime,
  PageHeader,
  PROJECT_STATUS,
  StatCard,
  StatGrid,
  StatusChip,
  statusOf,
} from '../../components/ui';

const PROJECT_ROLES = [
  { value: 'project_owner', label: '项目负责人' },
  { value: 'project_engineer', label: '项目工程师' },
  { value: 'project_reviewer', label: '复核员' },
  { value: 'project_viewer', label: '观察员' },
];
const roleLabel = (code: string) => PROJECT_ROLES.find((r) => r.value === code)?.label ?? code;

export function ProjectsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const usersQ = useQuery({ queryKey: ['users'], queryFn: listUsers });

  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [detail, setDetail] = useState<Project | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [createForm] = Form.useForm();
  const [memberForm] = Form.useForm();

  const projects = projectsQ.data ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return projects;
    return projects.filter(
      (p) => p.name.toLowerCase().includes(q) || p.code.toLowerCase().includes(q),
    );
  }, [projects, search]);

  const activeCount = projects.filter((p) => p.status === 'active').length;

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['projects'] });
    if (detail) {
      queryClient.invalidateQueries({ queryKey: ['project-members', detail.id] });
    }
  };

  const createMut = useMutation({
    mutationFn: (v: { code: string; name: string; description?: string }) => createProject(v),
    onSuccess: () => {
      message.success('项目已创建');
      setCreateOpen(false);
      createForm.resetFields();
      invalidate();
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '创建失败'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteProject(id),
    onSuccess: () => { message.success('项目已删除'); invalidate(); },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
  });

  const addMemberMut = useMutation({
    mutationFn: (v: { user_id: number; project_role: string }) =>
      addProjectMember(detail!.id, v),
    onSuccess: () => {
      message.success('成员已添加');
      memberForm.resetFields();
      loadMembers(detail!);
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '添加失败'),
  });

  const removeMemberMut = useMutation({
    mutationFn: (memberId: number) => removeProjectMember(detail!.id, memberId),
    onSuccess: () => { message.success('成员已移除'); loadMembers(detail!); },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '移除失败'),
  });

  async function loadMembers(p: Project) {
    setDetail(p);
    setMembersLoading(true);
    try {
      setMembers(await listProjectMembers(p.id));
    } catch {
      setMembers([]);
    }
    setMembersLoading(false);
  }

  const userMap = useMemo(() => {
    const m = new Map<number, string>();
    for (const u of usersQ.data ?? []) m.set(u.id, u.real_name || u.username);
    return m;
  }, [usersQ.data]);

  const columns = [
    { title: '#', dataIndex: 'id', width: 56, align: 'center' as const },
    {
      title: '编号', dataIndex: 'code', width: 140,
      render: (v: string) => <Typography.Text code>{v}</Typography.Text>,
    },
    {
      title: '名称', dataIndex: 'name',
      render: (v: string, r: Project) => (
        <Space>
          <span style={{ fontWeight: 500 }}>{v}</span>
          {r.description && (
            <Tooltip title={r.description}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>— {r.description}</Typography.Text>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '负责人', dataIndex: 'owner_id', width: 120,
      render: (v?: number | null) => (
        <Typography.Text type="secondary">{v ? userMap.get(v) ?? `#${v}` : '—'}</Typography.Text>
      ),
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (v: string) => <StatusChip style={statusOf(PROJECT_STATUS, v)} />,
    },
    {
      title: '创建时间', dataIndex: 'created_at', width: 160,
      render: (v: string) => <Typography.Text type="secondary" style={{ fontSize: 13 }}>{fmtDateTime(v)}</Typography.Text>,
    },
    {
      title: '操作', width: 120, align: 'center' as const,
      render: (_: unknown, r: Project) => (
        <Space size={2}>
          <Tooltip title="查看 / 成员管理">
            <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => loadMembers(r)} />
          </Tooltip>
          {r.status === 'active' && (
            <Popconfirm
              title="删除项目"
              description="将软删除该项目，可由管理员恢复。"
              onConfirm={() => deleteMut.mutate(r.id)}
              okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
            >
              <Tooltip title="删除">
                <Button type="text" size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="项目"
        subtitle="管理你参与的项目与成员"
        extra={
          <Space>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索名称 / 编号"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 220 }}
            />
            <Button icon={<ReloadOutlined />} onClick={() => projectsQ.refetch()} loading={projectsQ.isFetching} />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建项目</Button>
          </Space>
        }
      />

      <StatGrid>
        <StatCard label="项目总数" value={projects.length} icon={<ProjectOutlined />} color="#1677ff" bg="#e6f4ff" />
        <StatCard label="活跃项目" value={activeCount} icon={<TeamOutlined />} color="#52c41a" bg="#f6ffed" />
        <StatCard label="已归档 / 删除" value={projects.length - activeCount} icon={<DeleteOutlined />} color="#8c8c8c" bg="#fafafa" />
      </StatGrid>

      <Table
        className="surface-table"
        rowKey="id"
        dataSource={filtered}
        columns={columns}
        loading={projectsQ.isLoading}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 个项目` }}
        locale={{ emptyText: '暂无项目，点击右上角「新建项目」开始' }}
        scroll={{ x: 900 }}
      />

      {/* create drawer */}
      <Drawer
        title="新建项目"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        width={420}
        destroyOnHidden
        extra={
          <Button type="primary" loading={createMut.isPending} onClick={() => createForm.submit()}>
            创建
          </Button>
        }
      >
        <Form
          layout="vertical"
          form={createForm}
          onFinish={(v) => createMut.mutate(v)}
          requiredMark={false}
        >
          <Form.Item name="code" label="项目编号" rules={[
            { required: true, message: '请输入项目编号' },
            { pattern: /^[A-Za-z0-9_-]+$/, message: '仅允许字母、数字、下划线、连字符' },
            { max: 64, message: '不超过 64 字符' },
          ]}>
            <Input placeholder="如 PRJ-2026-001" />
          </Form.Item>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }, { max: 128 }]}>
            <Input placeholder="如 滨海中心一期图纸" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="选填" maxLength={500} showCount />
          </Form.Item>
        </Form>
      </Drawer>

      {/* detail / members drawer */}
      <Drawer
        title={detail ? `项目 · ${detail.name}` : '项目详情'}
        open={detail !== null}
        onClose={() => { setDetail(null); setMembers([]); }}
        width={560}
        loading={membersLoading}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 20 }}>
              <Descriptions.Item label="编号"><Typography.Text code>{detail.code}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
              <Descriptions.Item label="描述">{detail.description || '—'}</Descriptions.Item>
              <Descriptions.Item label="负责人">{detail.owner_id ? userMap.get(detail.owner_id) ?? `#${detail.owner_id}` : '—'}</Descriptions.Item>
              <Descriptions.Item label="状态"><StatusChip style={statusOf(PROJECT_STATUS, detail.status)} /></Descriptions.Item>
              <Descriptions.Item label="创建时间">{fmtDateTime(detail.created_at)}</Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5} style={{ marginTop: 8 }}>
              <TeamOutlined /> 成员 ({members.length})
            </Typography.Title>

            <Form
              layout="inline"
              form={memberForm}
              onFinish={(v) => addMemberMut.mutate(v)}
              style={{ marginBottom: 12, flexWrap: 'wrap', gap: 8 }}
            >
              <Form.Item name="user_id" rules={[{ required: true, message: '选择用户' }]} style={{ marginBottom: 0 }}>
                <Input placeholder="用户 ID" style={{ width: 120 }} type="number" />
              </Form.Item>
              <Form.Item name="project_role" initialValue="project_engineer" rules={[{ required: true }]} style={{ marginBottom: 0 }}>
                <Input.Group style={{ width: 150 }}>
                  <select className="ant-input" defaultValue="project_engineer" onChange={(e) => memberForm.setFieldValue('project_role', e.target.value)}>
                    {PROJECT_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                </Input.Group>
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={addMemberMut.isPending} icon={<PlusOutlined />}>添加</Button>
            </Form>

            <Table
              rowKey="id"
              dataSource={members}
              size="small"
              pagination={false}
              loading={membersLoading}
              locale={{ emptyText: '暂无成员' }}
              columns={[
                { title: '用户', dataIndex: 'user_id', render: (v: number) => userMap.get(v) ?? `#${v}` },
                {
                  title: '项目角色', dataIndex: 'project_role', width: 140,
                  render: (v: string) => <Tag color="blue">{roleLabel(v)}</Tag>,
                },
                {
                  title: '', width: 60, align: 'center' as const,
                  render: (_: unknown, m: ProjectMember) => (
                    <Popconfirm title="移除该成员？" onConfirm={() => removeMemberMut.mutate(m.id)} okText="移除" cancelText="取消" okButtonProps={{ danger: true }}>
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  ),
                },
              ]}
            />
          </>
        )}
      </Drawer>
    </>
  );
}
