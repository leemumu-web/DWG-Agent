import { useMemo, useState } from 'react';
import { App, Avatar, Breadcrumb, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd';
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DashboardOutlined,
  FileOutlined,
  TeamOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
  LogoutOutlined,
  ProfileOutlined,
  MenuOutlined,
  ApartmentOutlined,
  DatabaseOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { logout } from '../shared/auth';
import { useAuthStore } from '../shared/auth';
import { roleColor } from '../shared/components';

const { Header, Sider, Content } = Layout;

interface NavItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  roles?: string[]; // when set, only show if user has one of these (super_admin always passes)
}

const NAV: NavItem[] = [
  { key: '/dashboard', label: '工作台', icon: <DashboardOutlined /> },
  { key: '/workflows', label: '生产流程', icon: <ApartmentOutlined /> },
  { key: '/files', label: '文件转换', icon: <FileOutlined /> },
  { key: '/remnants', label: '余料库', icon: <InboxOutlined />, roles: ['admin', 'operator'] },
  { key: '/admin/users', label: '用户管理', icon: <TeamOutlined />, roles: ['admin'] },
  { key: '/admin/roles', label: '角色权限', icon: <SafetyCertificateOutlined />, roles: ['admin'] },
  { key: '/admin/infrastructure', label: '数据与存储', icon: <DatabaseOutlined />, roles: ['admin'] },
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
        <div className="app-brand" style={{ paddingInline: navCollapsed ? 23 : 20 }}>
          <div className="app-brand-mark">DW</div>
          {!navCollapsed && (
            <div>
              <div className="app-brand-name">DWG-Agent</div>
              <div className="app-brand-subtitle">CAD 智能处理平台</div>
            </div>
          )}
        </div>
        <Menu
          className="app-nav"
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
    <Layout className="app-shell">
      {!isMobile && (
        <Sider
          width={232}
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          trigger={null}
          className="app-sider"
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
        <Header className="app-topbar">
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
                className="user-trigger"
                style={{ padding: isMobile ? 4 : undefined }}
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

        <Content className="app-content">
          <main className="app-content-inner" aria-live="polite">
            <Outlet />
          </main>
        </Content>
      </Layout>
    </Layout>
  );
}
