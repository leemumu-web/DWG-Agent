import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from './layout';
import { LoginPage } from '../features/auth/LoginPage';
import { DashboardPage } from '../features/dashboard/DashboardPage';
import { ProjectsPage } from '../features/projects/ProjectsPage';
import { FilesLayout } from '../features/files/FilesLayout';
import { Dwg2DxfPage } from '../features/files/Dwg2DxfPage';
import { Dxf2DwgPage } from '../features/files/Dxf2DwgPage';
import { DrawingsPage } from '../features/drawings/DrawingsPage';
import { JobsPage } from '../features/jobs/JobsPage';
import { ReviewsPage } from '../features/reviews/ReviewsPage';
import { UsersPage } from '../features/users/UsersPage';
import { AuditLogsPage } from '../features/admin/AuditLogsPage';
import { RolesPage } from '../features/admin/RolesPage';
import { ProfilePage } from '../features/profile/ProfilePage';
import { RequireAuth, RequireRoles } from '../components/PermissionGuard';

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/files" element={<FilesLayout />}>
              <Route index element={<Navigate to="/files/dwg2dxf" replace />} />
              <Route path="dwg2dxf" element={<Dwg2DxfPage />} />
              <Route path="dxf2dwg" element={<Dxf2DwgPage />} />
            </Route>
            <Route path="/drawings" element={<DrawingsPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            {/* /reviews/pending is "authenticated" per spec §1.9 — admins see all,
                other users see pending reviews scoped to their projects. */}
            <Route path="/reviews" element={<ReviewsPage />} />
            <Route element={<RequireRoles allowed={['admin']} />}>
              <Route path="/admin/users" element={<UsersPage />} />
              <Route path="/admin/roles" element={<RolesPage />} />
            </Route>
            <Route element={<RequireRoles allowed={['auditor']} />}>
              <Route path="/admin/audit-logs" element={<AuditLogsPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
