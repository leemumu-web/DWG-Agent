import { useMemo } from 'react';
import { Button, Card, Empty, Space, Tag, Tooltip, Typography } from 'antd';
import {
  SafetyCertificateOutlined,
  KeyOutlined,
  ReloadOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { listPermissions, listRoles } from './roles.api';
import type { Permission, Role } from '../../shared/auth';
import { PageHeader, StatCard, StatGrid } from '../../shared/components';

const SYSTEM_ROLE_COLOR: Record<string, string> = {
  super_admin: 'magenta',
  admin: 'red',
  engineer: 'blue',
  reviewer: 'purple',
  operator: 'cyan',
  viewer: 'default',
  auditor: 'gold',
};

export function RolesPage() {
  const rolesQ = useQuery({ queryKey: ['roles'], queryFn: listRoles });
  const permsQ = useQuery({ queryKey: ['permissions'], queryFn: listPermissions });

  const roles = rolesQ.data ?? [];
  const permissions = permsQ.data ?? [];

  // group permissions by resource
  const byResource = useMemo(() => {
    const m = new Map<string, Permission[]>();
    for (const p of permissions) {
      const arr = m.get(p.resource) ?? [];
      arr.push(p);
      m.set(p.resource, arr);
    }
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [permissions]);

  const actionColor = (action: string): string => {
    if (action === 'read' || action === 'list' || action === 'view') return 'blue';
    if (action === 'create') return 'green';
    if (action === 'update' || action === 'patch') return 'orange';
    if (action === 'delete') return 'red';
    if (action === 'manage') return 'magenta';
    return 'default';
  };

  return (
    <>
      <PageHeader
        title="角色与权限"
        subtitle="系统角色定义与可用权限清单"
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => { rolesQ.refetch(); permsQ.refetch(); }}
            loading={rolesQ.isFetching || permsQ.isFetching}
          />
        }
      />

      <StatGrid>
        <StatCard label="角色" value={roles.length} icon={<SafetyCertificateOutlined />} color="#1677ff" bg="#e6f4ff" />
        <StatCard label="权限项" value={permissions.length} icon={<KeyOutlined />} color="#722ed1" bg="#f9f0ff" />
        <StatCard label="资源类型" value={byResource.length} icon={<AppstoreOutlined />} color="#13c2c2" bg="#e6fffb" />
      </StatGrid>

      {/* roles as cards */}
      <Typography.Title level={5}>系统角色</Typography.Title>
      {roles.length === 0 ? (
        <Empty description="暂无角色" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, marginBottom: 28 }}>
          {roles.map((r: Role) => (
            <Card key={r.id} size="small" styles={{ body: { padding: 16 } }} style={{ borderRadius: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <Space>
                  <Tag color={SYSTEM_ROLE_COLOR[r.code] ?? 'default'} style={{ margin: 0 }}>{r.code}</Tag>
                  <Typography.Text strong>{r.name}</Typography.Text>
                </Space>
                {r.is_system && <Tag color="default">系统内置</Tag>}
              </div>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 13, minHeight: 20 }}>
                {r.description || '无描述'}
              </Typography.Paragraph>
            </Card>
          ))}
        </div>
      )}

      {/* permissions grouped by resource */}
      <Typography.Title level={5}>权限清单（按资源分组）</Typography.Title>
      {byResource.length === 0 ? (
        <Empty description="暂无权限" />
      ) : (
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {byResource.map(([resource, items]) => (
            <Card
              key={resource}
              size="small"
              title={<Typography.Text strong><Tag color="geekblue" style={{ margin: 0 }}>{resource}</Tag></Typography.Text>}
              styles={{ body: { padding: '12px 16px' } }}
              style={{ borderRadius: 10 }}
            >
              <Space size={8} wrap>
                {items.map((p) => (
                  <Tooltip key={p.id} title={p.name}>
                    <Tag color={actionColor(p.action)} style={{ margin: 0 }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{p.action}</span>
                      <span style={{ marginLeft: 6, color: 'rgba(0,0,0,0.45)' }}>{p.name}</span>
                    </Tag>
                  </Tooltip>
                ))}
              </Space>
            </Card>
          ))}
        </Space>
      )}
    </>
  );
}
