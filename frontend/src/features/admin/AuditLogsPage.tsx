import { useMemo, useState } from 'react';
import {
  Button,
  Descriptions,
  Drawer,
  Input,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  ReloadOutlined,
  SearchOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  GlobalOutlined,
  DesktopOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { listAuditLogsPage } from '../../api/audit-logs.api';
import type { AuditLog } from '../../types/audit';
import { fmtDateTime, fmtRelative, PageHeader, StatCard, StatGrid } from '../../components/ui';

// action → color, by verb prefix
function actionColor(action: string): string {
  if (action.endsWith('.create')) return 'green';
  if (action.endsWith('.update') || action.endsWith('.update_self')) return 'orange';
  if (action.endsWith('.delete')) return 'red';
  if (action.endsWith('.disable')) return 'volcano';
  if (action.endsWith('.enable')) return 'cyan';
  if (action.endsWith('.login') || action.endsWith('.logout')) return 'blue';
  if (action.endsWith('.password_change') || action.endsWith('.password_reset')) return 'magenta';
  if (action.endsWith('.download') || action.endsWith('.download_url')) return 'geekblue';
  return 'default';
}

function actionLabel(action: string): string {
  // auth.login → 登录 ; users.create → 创建用户
  const [domain, verb] = action.split('.');
  const verbMap: Record<string, string> = {
    create: '创建', update: '更新', update_self: '更新本人',
    delete: '删除', disable: '禁用', enable: '启用',
    login: '登录', logout: '登出',
    password_change: '改密', password_reset: '重置密码',
    download: '下载', download_url: '下载链接',
    roles_add: '加角色', roles_remove: '移除角色',
  };
  const domainMap: Record<string, string> = {
    auth: '认证', users: '用户', projects: '项目', project_members: '项目成员',
    files: '文件', drawings: '图纸', drawing_versions: '图纸版本',
    jobs: '任务', reviews: '复核', roles: '角色',
  };
  return `${domainMap[domain] ?? domain}·${verbMap[verb] ?? verb}`;
}

function prettyJson(v: unknown): string {
  if (v == null) return '—';
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export function AuditLogsPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [domainFilter, setDomainFilter] = useState<string>('all');
  const [detail, setDetail] = useState<AuditLog | null>(null);
  const logsQ = useQuery({
    queryKey: ['audit-logs', page, pageSize, domainFilter, search],
    queryFn: () => listAuditLogsPage({
      page,
      page_size: pageSize,
      action_domain: domainFilter === 'all' ? undefined : domainFilter,
      search: search || undefined,
    }),
  });

  const logs = logsQ.data?.data ?? [];

  const domains = useMemo(
    () => Array.from(new Set([
      'auth', 'users', 'projects', 'project_members', 'files', 'jobs',
      'drawings', 'drawing_versions', 'reviews', 'roles', 'storage',
      ...logs.map((log) => log.action.split('.')[0]),
    ])).sort(),
    [logs],
  );

  const uniqueActors = useMemo(() => new Set(logs.map((l) => l.actor_user_id).filter(Boolean)).size, [logs]);

  const columns = [
    { title: '#', dataIndex: 'id', width: 64, align: 'center' as const },
    {
      title: '动作', dataIndex: 'action', width: 160,
      render: (v: string) => <Tag color={actionColor(v)}>{actionLabel(v)}</Tag>,
    },
    {
      title: '资源', width: 160,
      render: (_: unknown, r: AuditLog) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{r.resource_type}</Typography.Text>
          {r.resource_id != null && <Typography.Text type="secondary" style={{ fontSize: 12 }}>#{r.resource_id}</Typography.Text>}
        </Space>
      ),
    },
    {
      title: '操作人', dataIndex: 'actor_user_id', width: 100,
      render: (v?: number | null) => v != null ? <Typography.Text code>#{v}</Typography.Text> : <Typography.Text type="secondary">系统</Typography.Text>,
    },
    {
      title: '来源 IP', dataIndex: 'ip_address', width: 130,
      render: (v?: string | null) => v ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v}</Typography.Text> : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '时间', dataIndex: 'created_at', width: 160,
      render: (v: string) => (
        <Tooltip title={fmtDateTime(v)}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>{fmtRelative(v)}</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '操作', width: 70, align: 'center' as const,
      render: (_: unknown, r: AuditLog) => (
        <Button type="text" size="small" onClick={() => setDetail(r)}>详情</Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="审计日志"
        subtitle="平台关键操作留痕，支持服务端筛选与逐页追溯"
        extra={
          <Space>
            <Input.Search allowClear prefix={<SearchOutlined />} placeholder="搜索动作 / 资源 / ID" value={draftSearch} onChange={(event) => setDraftSearch(event.target.value)} onSearch={(value) => { setSearch(value.trim()); setPage(1); }} style={{ width: 240 }} />
            <Button icon={<ReloadOutlined />} onClick={() => logsQ.refetch()} loading={logsQ.isFetching} />
          </Space>
        }
      />

      <StatGrid>
        <StatCard label="日志总数" value={logsQ.data?.pagination.total ?? 0} icon={<HistoryOutlined />} color="#1677ff" bg="#e6f4ff" hint="按当前筛选条件统计" />
        <StatCard label="本页操作人" value={uniqueActors} icon={<FileSearchOutlined />} color="#722ed1" bg="#f9f0ff" />
        <StatCard label="本页资源类型" value={new Set(logs.map((l) => l.resource_type)).size} icon={<DesktopOutlined />} color="#13c2c2" bg="#e6fffb" />
      </StatGrid>

      {domains.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Segmented
            value={domainFilter}
            onChange={(value) => { setDomainFilter(value as string); setPage(1); }}
            options={[{ label: '全部', value: 'all' }, ...domains.map((d) => ({ label: d, value: d }))]}
          />
        </div>
      )}

      <Table
        rowKey="id"
        dataSource={logs}
        columns={columns}
        loading={logsQ.isLoading}
        size="middle"
        pagination={{ current: page, pageSize, total: logsQ.data?.pagination.total ?? 0, showSizeChanger: true, showTotal: (total) => `共 ${total} 条`, onChange: (nextPage, nextPageSize) => { setPage(nextPageSize === pageSize ? nextPage : 1); setPageSize(nextPageSize); } }}
        locale={{ emptyText: '暂无审计日志' }}
        style={{ background: '#fff', borderRadius: 10 }}
      />

      <Drawer
        title={detail ? `审计日志 #${detail.id}` : '日志详情'}
        open={detail !== null}
        onClose={() => setDetail(null)}
        width={560}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 20 }}>
              <Descriptions.Item label="动作"><Tag color={actionColor(detail.action)}>{actionLabel(detail.action)}</Tag></Descriptions.Item>
              <Descriptions.Item label="资源">{detail.resource_type}{detail.resource_id != null ? ` #${detail.resource_id}` : ''}</Descriptions.Item>
              <Descriptions.Item label="操作人">{detail.actor_user_id != null ? `#${detail.actor_user_id}` : '系统'}</Descriptions.Item>
              <Descriptions.Item label="来源 IP">
                {detail.ip_address ? <span><GlobalOutlined /> {detail.ip_address}</span> : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="User-Agent">
                {detail.user_agent ? <Typography.Text type="secondary" style={{ fontSize: 12, wordBreak: 'break-all' }}>{detail.user_agent}</Typography.Text> : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="时间">{fmtDateTime(detail.created_at)}</Descriptions.Item>
            </Descriptions>

            {detail.before_json && (
              <div style={{ marginBottom: 16 }}>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>变更前</Typography.Text>
                <pre style={{ background: '#fff7e6', border: '1px solid #ffe58f', borderRadius: 8, padding: 12, fontSize: 12, overflow: 'auto', maxHeight: 240 }}>
{prettyJson(detail.before_json)}
                </pre>
              </div>
            )}
            {detail.after_json && (
              <div>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>变更后</Typography.Text>
                <pre style={{ background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 8, padding: 12, fontSize: 12, overflow: 'auto', maxHeight: 240 }}>
{prettyJson(detail.after_json)}
                </pre>
              </div>
            )}
            {!detail.before_json && !detail.after_json && (
              <Typography.Text type="secondary">该记录无变更前后数据。</Typography.Text>
            )}
          </>
        )}
      </Drawer>
    </>
  );
}
