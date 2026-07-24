import { lazy, Suspense } from 'react';
import { Spin } from 'antd';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from './layout';
import { RequireAuth, RequireRoles } from '../shared/auth';

const LoginPage = lazy(() => import('../features/identity').then((module) => ({ default: module.LoginPage })));
const DashboardPage = lazy(() => import('../features/dashboard').then((module) => ({ default: module.DashboardPage })));
const ProjectsPage = lazy(() => import('../features/projects').then((module) => ({ default: module.ProjectsPage })));
const FilesLayout = lazy(() => import('../features/files').then((module) => ({ default: module.FilesLayout })));
const Dwg2DxfPage = lazy(() => import('../features/cad-processing').then((module) => ({ default: module.Dwg2DxfPage })));
const Dxf2DwgPage = lazy(() => import('../features/cad-processing').then((module) => ({ default: module.Dxf2DwgPage })));
const Dxf2ExcelPage = lazy(() => import('../features/cad-processing').then((module) => ({ default: module.Dxf2ExcelPage })));
const ExcelFinalPage = lazy(() => import('../features/excel-processing').then((module) => ({ default: module.ExcelFinalPage })));
const DrawingsPage = lazy(() => import('../features/projects').then((module) => ({ default: module.DrawingsPage })));
const JobsPage = lazy(() => import('../features/jobs').then((module) => ({ default: module.JobsPage })));
const ReviewsPage = lazy(() => import('../features/reviews').then((module) => ({ default: module.ReviewsPage })));
const UsersPage = lazy(() => import('../features/identity').then((module) => ({ default: module.UsersPage })));
const AuditLogsPage = lazy(() => import('../features/operations').then((module) => ({ default: module.AuditLogsPage })));
const RolesPage = lazy(() => import('../features/identity').then((module) => ({ default: module.RolesPage })));
const ProfilePage = lazy(() => import('../features/identity').then((module) => ({ default: module.ProfilePage })));
const WorkflowsPage = lazy(() => import('../features/workflows').then((module) => ({ default: module.WorkflowsPage })));
const WorkflowDetailPage = lazy(() => import('../features/workflows').then((module) => ({ default: module.WorkflowDetailPage })));
const InfrastructurePage = lazy(() => import('../features/operations').then((module) => ({ default: module.InfrastructurePage })));
const RemnantInventoryPage = lazy(() => import('../features/remnant-inventory').then((module) => ({ default: module.RemnantInventoryPage })));

export function AppRouter() {
  return (
    <BrowserRouter>
      <Suspense fallback={<div style={{ display: 'grid', placeItems: 'center', minHeight: 240 }}><Spin /></div>}>
        <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/workflows/:workflowId" element={<WorkflowDetailPage />} />
            <Route path="/files" element={<FilesLayout />}>
              <Route index element={<Navigate to="/files/dwg2dxf" replace />} />
              <Route path="dwg2dxf" element={<Dwg2DxfPage />} />
              <Route path="dxf2dwg" element={<Dxf2DwgPage />} />
              <Route path="dxf2excel" element={<Dxf2ExcelPage />} />
              <Route path="excel-final" element={<ExcelFinalPage />} />
            </Route>
            <Route path="/drawings" element={<DrawingsPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            {/* /reviews/pending is "authenticated" per spec §1.9 — admins see all,
                other users see pending reviews scoped to their projects. */}
            <Route path="/reviews" element={<ReviewsPage />} />
            <Route element={<RequireRoles allowed={['admin', 'remnant_worker']} />}>
              <Route path="/remnants" element={<RemnantInventoryPage />} />
            </Route>
            <Route element={<RequireRoles allowed={['admin']} />}>
              <Route path="/admin/users" element={<UsersPage />} />
              <Route path="/admin/roles" element={<RolesPage />} />
            </Route>
            <Route element={<RequireRoles allowed={['admin', 'auditor']} />}>
              <Route path="/admin/infrastructure" element={<InfrastructurePage />} />
            </Route>
            <Route element={<RequireRoles allowed={['auditor']} />}>
              <Route path="/admin/audit-logs" element={<AuditLogsPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
