import { useMemo, useState } from 'react';
import { App, Avatar, Breadcrumb, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd';
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DashboardOutlined,
  ProjectOutlined,
  FileOutlined,
  FileImageOutlined,
  CloudOutlined,
  AuditOutlined,
  TeamOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
  LogoutOutlined,
  ProfileOutlined,
  MenuOutlined,
} from '@ant-design/icons';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { logout } from '../api/auth.api';
import { useAuthStore } from '../stores/auth.store';
import { roleColor } from '../components/ui';

const { Header, Sider, Content } = Layout;

interface NavItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  roles?: string[]; // when set, only show if user has one of these (super_admin always passes)
}

const NAV: NavItem[] = [
  { key: '/dashboard', label: '工作台', icon: <DashboardOutlined /> },
  { key: '/projects', label: '项目', icon: <ProjectOutlined /> },
  { key: '/files', label: '文件转换', icon: <FileOutlined /> },
  { key: '/drawings', label: '图纸', icon: <FileImageOutlined /> },
  { key: '/jobs', label: '任务', icon: <CloudOutlined /> },
  { key: '/reviews', label: '复核', icon: <AuditOutlined /> },
  { key: '/admin/users', label: '用户管理', icon: <TeamOutlined />, roles: ['admin'] },
  { key: '/admin/roles', label: '角色权限', icon: <SafetyCertificateOutlined />, roles: ['admin'] },
  { key: '/admin/audit-logs', label: '审计日志', icon: <ProfileOutlined />, roles: ['auditor'] },
];

/** Build a 1-2 segment breadcrumb from the active path. */
function useBreadcrumb() {
  const loc = useLocation();
  return useMemo(() => {
    const seg = loc.pathname.split('/').filter(Boolean);
    const crumbs: { title: string }[] = [{ title: 'DWG-Agent' }];
    const byKey: Record<string, string> = {};
    for (const n of NAV) byKey[n.key] = n.label;
    if (seg[0] === 'admin' && seg[1]) {
      const full = `/${seg[0]}/${seg[1]}`;
      crumbs.push({ title: '管理' });
      crumbs.push({ title: byKey[full] ?? seg[1] });
    } else if (seg[0] && byKey[`/${seg[0]}`]) {
      crumbs.push({ title: byKey[`/${seg[0]}`] });
    } else if (seg[0]) {
      crumbs.push({ title: seg[0] });
    }
    return crumbs;
  }, [loc.pathname]);
}

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;

  const user = useAuthStore((s) => s.user);
  const clearSession = useAuthStore((s) => s.clearSession);

  const roleCodes = new Set(user?.roles.map((r) => r.code) ?? []);
  const isSuper = roleCodes.has('super_admin');

  const items = NAV.filter((n) => {
    if (!n.roles) return true;
    return isSuper || n.roles.some((r) => roleCodes.has(r));
  }).map((n) => ({
    key: n.key,
    icon: n.icon,
    label: <Link to={n.key}>{n.label}</Link>,
  }));

  // collapse long nav into matching the longest active prefix (e.g. /admin/users under /admin/users)
  const selected = useMemo(() => {
    const exact = items.find((i) => i.key === location.pathname);
    if (exact) return [exact.key];
    const match = items
      .map((i) => i.key)
      .filter((k) => location.pathname.startsWith(k))
      .sort((a, b) => b.length - a.length)[0];
    return match ? [match] : [];
  }, [location.pathname, items]);

  const breadcrumb = useBreadcrumb();

  async function onLogout() {
    try {
      await logout();
    } catch {
      // ignore network failures — clear local session anyway
    }
    clearSession();
    queryClient.clear();
    message.success('已登出');
    navigate('/login', { replace: true });
  }

  const initial = (user?.real_name || user?.username || '?').slice(0, 1).toUpperCase();

  function navigationContent(navCollapsed: boolean, onSelect?: () => void) {
    return (
      <>
        <div
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: navCollapsed ? '0 16px' : '0 20px',
            overflow: 'hidden',
            borderBottom: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 8,
              background: 'linear-gradient(135deg,#1677ff,#13c2c2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 700,
              fontSize: 13,
              flexShrink: 0,
            }}
          >
            DW
          </div>
          {!navCollapsed && (
            <span style={{ color: '#fff', fontSize: 16, fontWeight: 600, whiteSpace: 'nowrap' }}>
              DWG-Agent
            </span>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selected}
          items={items}
          onClick={onSelect}
          style={{ borderRight: 0 }}
        />
      </>
    );
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!isMobile && (
        <Sider
          width={232}
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          trigger={null}
          style={{ boxShadow: '2px 0 8px rgba(0,0,0,0.06)' }}
        >
          {navigationContent(collapsed)}
        </Sider>
      )}

      <Drawer
        open={isMobile && mobileNavOpen}
        placement="left"
        size={232}
        closable={false}
        onClose={() => setMobileNavOpen(false)}
        styles={{ body: { padding: 0, background: '#001529' } }}
      >
        {navigationContent(false, () => setMobileNavOpen(false))}
      </Drawer>

      <Layout style={{ minWidth: 0 }}>
        <Header
          style={{
            background: '#fff',
            padding: isMobile ? '0 12px' : '0 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
            boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
            gap: isMobile ? 8 : 16,
          }}
        >
          <Space size={isMobile ? 4 : 'middle'} align="center" style={{ flex: 1, minWidth: 0 }}>
            <Button
              type="text"
              aria-label={isMobile ? '打开导航' : collapsed ? '展开导航' : '收起导航'}
              icon={
                isMobile
                  ? <MenuOutlined />
                  : collapsed
                    ? <MenuUnfoldOutlined />
                    : <MenuFoldOutlined />
              }
              onClick={() => {
                if (isMobile) setMobileNavOpen(true);
                else setCollapsed(!collapsed);
              }}
            />
            <Breadcrumb
              items={isMobile ? breadcrumb.slice(-1) : breadcrumb}
              style={{ fontSize: 13, minWidth: 0 }}
            />
          </Space>

          <Space size={isMobile ? 4 : 'middle'} align="center">
            {!isMobile && (
              <Tooltip title="Stage 1 · 生产就绪骨架 · 本机开发版">
                <Tag color="blue" style={{ margin: 0 }}>Stage 1</Tag>
              </Tooltip>
            )}

            <Dropdown
              placement="bottomRight"
              menu={{
                items: [
                  {
                    key: 'profile',
                    icon: <UserOutlined />,
                    label: '个人中心',
                    onClick: () => navigate('/profile'),
                  },
                  { type: 'divider' as const },
                  {
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: '退出登录',
                    onClick: onLogout,
                  },
                ],
              }}
            >
              <Space
                size={8}
                aria-label="用户菜单"
                style={{ cursor: 'pointer', padding: isMobile ? 4 : '4px 8px', borderRadius: 8 }}
              >
                <Avatar size={30} style={{ background: '#1677ff', fontSize: 13 }}>{initial}</Avatar>
                {!isMobile && (
                  <div style={{ lineHeight: 1.2, display: 'flex', flexDirection: 'column' }}>
                    <Typography.Text style={{ fontSize: 13 }}>
                      {user?.real_name || user?.username}
                    </Typography.Text>
                    <span style={{ display: 'flex', gap: 4, marginTop: 2 }}>
                      {(user?.roles ?? []).slice(0, 2).map((r) => (
                        <Tag key={r.code} color={roleColor(r.code)} style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                          {r.code}
                        </Tag>
                      ))}
                    </span>
                  </div>
                )}
              </Space>
            </Dropdown>
          </Space>
        </Header>

        <Content
          style={{
            margin: 0,
            padding: isMobile ? 12 : 20,
            background: '#f5f5f5',
            overflow: 'auto',
          }}
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
