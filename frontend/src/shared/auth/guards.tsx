import { Navigate, Outlet } from 'react-router-dom';
import { Button, Result } from 'antd';
import { LockOutlined } from '@ant-design/icons';
import { useAuthStore } from './store';

export function RequireAuth() {
  const token = useAuthStore((s) => s.accessToken);
  if (!token) return <Navigate to="/login" replace />;
  return <Outlet />;
}

const ROLE_LABELS: Record<string, string> = {
  super_admin: '超级管理员',
  admin: '管理员',
  operator: '操作员',
  viewer: '只读用户',
};

function humanRoleList(codes: string[]): string {
  return codes.map((c) => ROLE_LABELS[c] ?? c).join(' / ');
}

/** Full-page "Access Denied" view shown when RequireRoles blocks a route. */
export function AccessDeniedPage({ allowedRoles }: { allowedRoles: string[] }) {
  const user = useAuthStore((s) => s.user);
  const userRoles = user?.roles.map((r) => r.code) ?? [];
  const userLabel = userRoles.length
    ? humanRoleList(userRoles)
    : '未分配角色';

  return (
    <Result
      status="403"
      icon={<LockOutlined style={{ color: '#faad14' }} />}
      title="权限不足"
      subTitle={
        <>
          你的角色为 <strong>{userLabel}</strong>，该页面需要{' '}
          <strong>{humanRoleList(allowedRoles)}</strong> 权限。
          <br />
          如需访问，请联系管理员提升你的角色等级。
        </>
      }
      extra={
        <Button type="primary" onClick={() => window.history.back()}>
          返回上一页
        </Button>
      }
    />
  );
}

export function RequireRoles({ allowed }: { allowed: string[] }) {
  const token = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);

  if (!token) return <Navigate to="/login" replace />;

  const roleCodes = new Set(user?.roles.map((role) => role.code) ?? []);
  if (roleCodes.has('super_admin') || allowed.some((role) => roleCodes.has(role))) {
    return <Outlet />;
  }

  // Show proper 403 page instead of silent redirect
  return <AccessDeniedPage allowedRoles={allowed} />;
}
