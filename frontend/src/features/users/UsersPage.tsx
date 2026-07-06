import { useMemo, useState } from 'react';
import {
  App,
  Button,
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
  ReloadOutlined,
  PlusOutlined,
  SearchOutlined,
  UserOutlined,
  TeamOutlined,
  CheckCircleOutlined,
  StopOutlined,
  KeyOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  assignRole,
  createUser,
  deleteUser,
  disableUser,
  enableUser,
  listUsers,
  removeRole,
  resetUserPassword,
  updateUser,
} from '../../api/users.api';
import { listRoles } from '../../api/roles.api';
import type { User } from '../../types/user';
import {
  fmtDateTime,
  PageHeader,
  roleColor,
  StatCard,
  StatGrid,
  StatusChip,
  statusOf,
  USER_STATUS,
} from '../../components/ui';

export function UsersPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const usersQ = useQuery({ queryKey: ['users'], queryFn: listUsers });
  const rolesQ = useQuery({ queryKey: ['roles'], queryFn: listRoles });

  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [roleForm] = Form.useForm();
  const [editing, setEditing] = useState<User | null>(null);
  const [tempPwd, setTempPwd] = useState<{ user: User; pwd: string } | null>(null);

  const users = usersQ.data ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.username.toLowerCase().includes(q) ||
        (u.real_name ?? '').toLowerCase().includes(q) ||
        (u.email ?? '').toLowerCase().includes(q) ||
        (u.employee_no ?? '').toLowerCase().includes(q),
    );
  }, [users, search]);

  const activeCount = users.filter((u) => u.status === 'active').length;
  const disabledCount = users.filter((u) => u.status === 'disabled').length;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['users'] });

  const createMut = useMutation({
    mutationFn: (v: { username: string; password: string; real_name: string; employee_no?: string; email?: string }) =>
      createUser(v),
    onSuccess: () => {
      message.success('用户已创建（请单独分配角色）');
      setCreateOpen(false);
      createForm.resetFields();
      invalidate();
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '创建失败'),
  });

  const toggleMut = useMutation({
    mutationFn: async (u: User) => (u.status === 'disabled' ? enableUser(u.id) : disableUser(u.id)),
    onSuccess: () => { message.success('状态已更新'); invalidate(); },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '操作失败'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => { message.success('用户已删除'); invalidate(); },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
  });

  const resetMut = useMutation({
    mutationFn: (id: number) => resetUserPassword(id),
    onSuccess: (data, id: number) => {
      const u = users.find((x) => x.id === id);
      if (u) setTempPwd({ user: u, pwd: data.temp_password });
      invalidate();
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '重置失败'),
  });

  const assignRoleMut = useMutation({
    mutationFn: ({ userId, roleCode }: { userId: number; roleCode: string }) =>
      assignRole(userId, roleCode),
    onSuccess: () => {
      message.success('角色已分配');
      roleForm.resetFields();
      invalidate();
      if (editing) {
        // refresh editing snapshot from cache after refetch settles
        setTimeout(() => {
          const fresh = usersQ.data?.find((u) => u.id === editing.id);
          if (fresh) setEditing(fresh);
        }, 200);
      }
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '分配失败'),
  });

  const removeRoleMut = useMutation({
    mutationFn: ({ userId, roleId }: { userId: number; roleId: number }) =>
      removeRole(userId, roleId),
    onSuccess: () => {
      message.success('角色已移除');
      invalidate();
      if (editing) {
        setTimeout(() => {
          const fresh = usersQ.data?.find((u) => u.id === editing.id);
          if (fresh) setEditing(fresh);
        }, 200);
      }
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '移除失败'),
  });

  const roleMap = useMemo(() => {
    const m = new Map<number, { id: number; code: string; name: string }>();
    for (const r of rolesQ.data ?? []) m.set(r.id, r);
    return m;
  }, [rolesQ.data]);

  const columns = [
    { title: '#', dataIndex: 'id', width: 56, align: 'center' as const },
    {
      title: '账号', dataIndex: 'username',
      render: (v: string, r: User) => (
        <Space>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, borderRadius: 8, background: '#e6f4ff', color: '#1677ff',
            fontSize: 13, fontWeight: 600,
          }}>{(r.real_name || v).slice(0, 1).toUpperCase()}</span>
          <div>
            <Typography.Text strong>{v}</Typography.Text>
            {r.employee_no && (
              <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.employee_no}</Typography.Text></div>
            )}
          </div>
        </Space>
      ),
    },
    { title: '姓名', dataIndex: 'real_name', width: 120 },
    {
      title: '邮箱', dataIndex: 'email',
      render: (v?: string | null) => v ? <Typography.Text type="secondary" style={{ fontSize: 13 }}>{v}</Typography.Text> : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '角色', dataIndex: 'roles', width: 200,
      render: (_: unknown, r: User) => (
        <Space size={4} wrap>
          {r.roles.length === 0 ? <Typography.Text type="secondary">—</Typography.Text> :
            r.roles.map((role) => <Tag key={role.code} color={roleColor(role.code)}>{role.name || role.code}</Tag>)}
        </Space>
      ),
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (v: string) => <StatusChip style={statusOf(USER_STATUS, v)} />,
    },
    {
      title: '创建时间', dataIndex: 'created_at', width: 150,
      render: (v: string) => <Typography.Text type="secondary" style={{ fontSize: 13 }}>{fmtDateTime(v)}</Typography.Text>,
    },
    {
      title: '操作', width: 200, align: 'center' as const,
      render: (_: unknown, r: User) => (
        <Space size={2}>
          <Tooltip title="角色管理"><Button type="text" size="small" icon={<KeyOutlined />} onClick={() => setEditing(r)} /></Tooltip>
          <Popconfirm
            title={r.status === 'disabled' ? '启用该用户？' : '禁用该用户？'}
            onConfirm={() => toggleMut.mutate(r)}
            okText="确定" cancelText="取消"
            disabled={r.status === 'deleted'}
          >
            <Tooltip title={r.status === 'disabled' ? '启用' : '禁用'}>
              <Button type="text" size="small" danger={r.status !== 'disabled'} icon={r.status === 'disabled' ? <CheckCircleOutlined /> : <StopOutlined />} disabled={r.status === 'deleted'} />
            </Tooltip>
          </Popconfirm>
          <Popconfirm
            title="重置密码"
            description="将生成临时密码并使该用户所有会话失效。"
            onConfirm={() => resetMut.mutate(r.id)}
            okText="重置" cancelText="取消"
            disabled={r.status === 'deleted'}
          >
            <Tooltip title="重置密码"><Button type="text" size="small" icon={<KeyOutlined />} disabled={r.status === 'deleted'} /></Tooltip>
          </Popconfirm>
          <Popconfirm
            title="删除用户"
            description="软删除，不可恢复自身。"
            onConfirm={() => deleteMut.mutate(r.id)}
            okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
            disabled={r.status === 'deleted'}
          >
            <Tooltip title="删除"><Button type="text" size="small" danger icon={<DeleteOutlined />} disabled={r.status === 'deleted'} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="用户管理"
        subtitle="管理账号、角色与状态"
        extra={
          <Space>
            <Input allowClear prefix={<SearchOutlined />} placeholder="搜索账号 / 姓名 / 邮箱" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 260 }} />
            <Button icon={<ReloadOutlined />} onClick={() => usersQ.refetch()} loading={usersQ.isFetching} />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建用户</Button>
          </Space>
        }
      />

      <StatGrid>
        <StatCard label="用户总数" value={users.length} icon={<TeamOutlined />} color="#1677ff" bg="#e6f4ff" />
        <StatCard label="正常" value={activeCount} icon={<CheckCircleOutlined />} color="#52c41a" bg="#f6ffed" />
        <StatCard label="已禁用" value={disabledCount} icon={<StopOutlined />} color="#faad14" bg="#fffbe6" />
      </StatGrid>

      <Table
        rowKey="id"
        dataSource={filtered}
        columns={columns}
        loading={usersQ.isLoading}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 个用户` }}
        locale={{ emptyText: '暂无用户' }}
        style={{ background: '#fff', borderRadius: 10 }}
      />

      {/* create drawer */}
      <Drawer
        title="新建用户"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        width={420}
        destroyOnClose
        extra={<Button type="primary" loading={createMut.isPending} onClick={() => createForm.submit()}>创建</Button>}
      >
        <Form layout="vertical" form={createForm} onFinish={(v) => createMut.mutate(v)} requiredMark={false}>
          <Form.Item name="username" label="账号" rules={[
            { required: true, message: '请输入账号' },
            { pattern: /^[a-zA-Z0-9_.@-]+$/, message: '仅允许字母、数字、_ . @ -' },
            { max: 64 },
          ]}>
            <Input placeholder="如 10025" />
          </Form.Item>
          <Form.Item name="real_name" label="姓名" rules={[{ required: true, message: '请输入姓名' }, { max: 64 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="employee_no" label="工号"><Input maxLength={64} /></Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ type: 'email', message: '邮箱格式不正确' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[
            { required: true, message: '请输入密码' },
            { min: 12, message: '至少 12 位' },
          ]} extra="至少 12 位，须含大写、小写、数字">
            <Input.Password placeholder="如 NewSecurePass123" />
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            创建后需通过「角色管理」单独分配系统角色。
          </Typography.Text>
        </Form>
      </Drawer>

      {/* role management drawer */}
      <Drawer
        title={editing ? `角色管理 · ${editing.real_name} (${editing.username})` : '角色管理'}
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={460}
      >
        {editing && (
          <>
            <Typography.Title level={5}>当前角色</Typography.Title>
            {editing.roles.length === 0 ? (
              <Typography.Text type="secondary">该用户暂无角色</Typography.Text>
            ) : (
              <Space size={8} wrap style={{ marginBottom: 8 }}>
                {editing.roles.map((r) => (
                  <Tag key={r.code} color={roleColor(r.code)} closable onClose={() => removeRoleMut.mutate({ userId: editing.id, roleId: r.id })}>
                    {r.name || r.code}
                  </Tag>
                ))}
              </Space>
            )}

            <Typography.Title level={5} style={{ marginTop: 20 }}>分配新角色</Typography.Title>
            <Form
              layout="inline"
              form={roleForm}
              onFinish={(v: { roleCode: string }) => assignRoleMut.mutate({ userId: editing.id, roleCode: v.roleCode })}
              style={{ gap: 8, flexWrap: 'wrap' }}
            >
              <Form.Item name="roleCode" rules={[{ required: true, message: '选择角色' }]} style={{ marginBottom: 0 }}>
                <select className="ant-input" style={{ width: 220 }} defaultValue="">
                  <option value="" disabled>选择角色…</option>
                  {(rolesQ.data ?? []).map((r) => <option key={r.code} value={r.code}>{r.name} ({r.code})</option>)}
                </select>
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={assignRoleMut.isPending} icon={<PlusOutlined />}>分配</Button>
            </Form>
          </>
        )}
      </Drawer>

      {/* temp password modal (drawer-as-modal) */}
      <Drawer
        title="临时密码"
        open={tempPwd !== null}
        onClose={() => setTempPwd(null)}
        width={420}
      >
        {tempPwd && (
          <div>
            <Typography.Paragraph>
              用户 <Typography.Text strong>{tempPwd.user.real_name}</Typography.Text> 的密码已重置。
              其所有活动会话已被注销。请将下方临时密码安全地传达给用户，登录后需修改。
            </Typography.Paragraph>
            <Input.Search
              value={tempPwd.pwd}
              readOnly
              enterButton="复制"
              onSearch={() => { navigator.clipboard?.writeText(tempPwd.pwd); message.success('已复制到剪贴板'); }}
              style={{ marginBottom: 12 }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              此密码仅显示一次，关闭后无法再次获取。
            </Typography.Text>
          </div>
        )}
      </Drawer>
    </>
  );
}
