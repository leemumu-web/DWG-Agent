import { useMemo } from 'react';
import { Button, Card, Descriptions, Empty, Progress, Space, Tag, Typography } from 'antd';
import {
  SafetyCertificateOutlined,
  KeyOutlined,
  ReloadOutlined,
  CrownOutlined,
  ToolOutlined,
  EyeOutlined,
  SafetyOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { listPermissions, listRoles } from './roles.api';
import type { Role } from '../../shared/auth';
import { PageHeader, StatCard, StatGrid } from '../../shared/components';

const ROLE_CONFIG: Record<string, { color: string; icon: React.ReactNode; tier: number; desc: string }> = {
  super_admin: {
    color: 'magenta',
    icon: <CrownOutlined />,
    tier: 1,
    desc: '全部管理权限：用户管理、角色分配、系统配置、审计日志、生产流程',
  },
  admin: {
    color: 'red',
    icon: <SafetyOutlined />,
    tier: 1,
    desc: '全部管理权限：用户管理、角色分配、系统配置、审计日志、生产流程',
  },
  operator: {
    color: 'cyan',
    icon: <ToolOutlined />,
    tier: 2,
    desc: '生产操作：工作流、文件上传、任务管理、复核提交、余料读写',
  },
  viewer: {
    color: 'default',
    icon: <EyeOutlined />,
    tier: 3,
    desc: '只读查看：审计日志、余料预览',
  },
};

const PERM_TIER_LABELS: Record<string, { title: string; subtitle: string }> = {
  admin: { title: '第一级 · 管理权限', subtitle: '用户/角色/系统/审计全部控制' },
  operator: { title: '第二级 · 操作权限', subtitle: '生产流程、文件、任务、复核、余料' },
  viewer: { title: '第三级 · 只读权限', subtitle: '审计日志查看、余料预览' },
};

function tierProgress(roleCode: string): number {
  const cfg = ROLE_CONFIG[roleCode];
  if (!cfg) return 0;
  if (cfg.tier === 1) return 100;
  if (cfg.tier === 2) return 66;
  return 33;
}

export function RolesPage() {
  const rolesQ = useQuery({ queryKey: ['roles'], queryFn: listRoles });
  const permsQ = useQuery({ queryKey: ['permissions'], queryFn: listPermissions });

  const roles = rolesQ.data ?? [];
  const permissions = permsQ.data ?? [];

  const tierGroups = useMemo(() => {
    const m = new Map<string, typeof permissions>();
    for (const p of permissions) {
      const arr = m.get(p.code) ?? [];
      arr.push(p);
      m.set(p.code, arr);
    }
    return m;
  }, [permissions]);

  return (
    <>
      <PageHeader
        title="角色与权限"
        subtitle="三级权限模型：管理 · 操作 · 只读"
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
        <StatCard label="权限等级" value={3} icon={<KeyOutlined />} color="#722ed1" bg="#f9f0ff" />
        <StatCard label="内置角色" value={roles.filter((r: Role) => r.is_system).length} icon={<SafetyOutlined />} color="#13c2c2" bg="#e6fffb" />
      </StatGrid>

      {/* ── 角色卡片 ── */}
      <Typography.Title level={5}>系统角色（{roles.length}）</Typography.Title>
      {roles.length === 0 ? (
        <Empty description="暂无角色" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, marginBottom: 28 }}>
          {roles.map((r: Role) => {
            const cfg = ROLE_CONFIG[r.code];
            return (
              <Card
                key={r.id}
                size="small"
                styles={{ body: { padding: 16 } }}
                style={{ borderRadius: 10, borderLeft: cfg ? `3px solid ${cfg.color === 'default' ? '#d9d9d9' : cfg.color}` : undefined }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                  <Space>
                    {cfg?.icon}
                    <div>
                      <Typography.Text strong style={{ fontSize: 15 }}>{r.name}</Typography.Text>
                      <br />
                      <Tag color={cfg?.color ?? 'default'} style={{ marginTop: 2 }}>{r.code}</Tag>
                    </div>
                  </Space>
                  {r.is_system && <Tag color="default" style={{ fontSize: 11 }}>系统内置</Tag>}
                </div>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 12, fontSize: 13, minHeight: 36 }}>
                  {r.description || cfg?.desc || '无描述'}
                </Typography.Paragraph>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Progress
                    percent={tierProgress(r.code)}
                    size="small"
                    showInfo={false}
                    strokeColor={cfg?.color === 'default' ? '#d9d9d9' : cfg?.color}
                    style={{ flex: 1, margin: 0 }}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                    {cfg ? `第${cfg.tier}级` : '-'}
                  </Typography.Text>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* ── 权限等级说明 ── */}
      <Typography.Title level={5}>权限等级说明</Typography.Title>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {['admin', 'operator', 'viewer'].map((permCode) => {
          const info = PERM_TIER_LABELS[permCode];
          if (!info) return null;
          return (
            <Card key={permCode} size="small" style={{ borderRadius: 10 }}>
              <Descriptions column={2} size="small" colon={false}>
                <Descriptions.Item label={<Typography.Text strong>{info.title}</Typography.Text>}>
                  {info.subtitle}
                </Descriptions.Item>
                <Descriptions.Item label="权限码">
                  <Tag color={permCode === 'admin' ? 'red' : permCode === 'operator' ? 'cyan' : 'default'}>
                    {permCode}
                  </Tag>
                </Descriptions.Item>
              </Descriptions>
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  拥有角色：
                </Typography.Text>
                {roles
                  .filter((r: Role) => r.permissions?.some((p) => p.code === permCode))
                  .map((r: Role) => {
                    const cfg = ROLE_CONFIG[r.code];
                    return (
                      <Tag key={r.id} color={cfg?.color} style={{ marginLeft: 6 }}>
                        {r.name}
                      </Tag>
                    );
                  })}
              </div>
            </Card>
          );
        })}
      </Space>
    </>
  );
}
