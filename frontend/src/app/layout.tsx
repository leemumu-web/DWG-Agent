import { Layout, Menu, Typography } from 'antd';
import { Link, Outlet, useLocation } from 'react-router-dom';

const { Header, Sider, Content } = Layout;

const items = [
  { key: '/dashboard', label: <Link to="/dashboard">工作台</Link> },
  { key: '/projects', label: <Link to="/projects">项目</Link> },
  { key: '/files', label: <Link to="/files">文件</Link> },
  { key: '/drawings', label: <Link to="/drawings">图纸</Link> },
  { key: '/jobs', label: <Link to="/jobs">任务</Link> },
  { key: '/reviews', label: <Link to="/reviews">复核</Link> },
  { key: '/admin/users', label: <Link to="/admin/users">用户管理</Link> },
  { key: '/admin/audit-logs', label: <Link to="/admin/audit-logs">审计日志</Link> },
];

export function AppLayout() {
  const location = useLocation();
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={240}>
        <div className="brand">
          <img src="/logo.png" alt="DWG-Agent Logo" className="brand-logo" />
          <span className="brand-text">DWG-Agent</span>
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={items} />
      </Sider>
      <Layout>
        <Header className="topbar">
          <Typography.Text strong>企业级 CAD 智能处理平台 · 本机开发版</Typography.Text>
        </Header>
        <Content className="content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
