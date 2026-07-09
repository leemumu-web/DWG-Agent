import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore } from '../stores/auth.store';

export function RequireAuth() {
  const token = useAuthStore((s) => s.accessToken);
  if (!token) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export function RequireRoles({ allowed }: { allowed: string[] }) {
  const token = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  if (!token) return <Navigate to="/login" replace />;

  const roleCodes = new Set(user?.roles.map((role) => role.code) ?? []);
  if (roleCodes.has('super_admin') || allowed.some((role) => roleCodes.has(role))) {
    return <Outlet />;
  }

  return <Navigate to="/dashboard" replace />;
}
