import type { ReactNode } from 'react';
import { Tooltip, Typography } from 'antd';

// ── status palettes ──────────────────────────────────────────────────────────
// Each entry maps a backend status string to a colored chip + human label.
// Used by jobs, files, projects, drawings, users, reviews.

export interface StatusStyle {
  color: string; // text / icon color
  bg: string; // chip background
  border: string;
  label: string;
}

const DEFAULT_STATUS: StatusStyle = {
  color: '#8c8c8c',
  bg: '#fafafa',
  border: '#f0f0f0',
  label: '未知',
};

export const JOB_STATUS: Record<string, StatusStyle> = {
  pending: { color: '#8c8c8c', bg: '#fafafa', border: '#f0f0f0', label: '待处理' },
  queued: { color: '#faad14', bg: '#fffbe6', border: '#fff1b8', label: '排队中' },
  running: { color: '#1677ff', bg: '#e6f4ff', border: '#bae0ff', label: '运行中' },
  waiting_cad_worker: { color: '#722ed1', bg: '#f9f0ff', border: '#d3adf7', label: '等待 CAD Worker' },
  validating: { color: '#08979c', bg: '#e6fffb', border: '#87e8de', label: '校验中' },
  need_review: { color: '#d48806', bg: '#fffbe6', border: '#ffe58f', label: '待复核' },
  succeeded: { color: '#52c41a', bg: '#f6ffed', border: '#b7eb8f', label: '成功' },
  failed: { color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7', label: '失败' },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', border: '#f0f0f0', label: '已取消' },
};

export const PROJECT_STATUS: Record<string, StatusStyle> = {
  active: { color: '#52c41a', bg: '#f6ffed', border: '#b7eb8f', label: '活跃' },
  archived: { color: '#8c8c8c', bg: '#fafafa', border: '#f0f0f0', label: '已归档' },
  deleted: { color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7', label: '已删除' },
};

export const USER_STATUS: Record<string, StatusStyle> = {
  active: { color: '#52c41a', bg: '#f6ffed', border: '#b7eb8f', label: '正常' },
  disabled: { color: '#faad14', bg: '#fffbe6', border: '#ffe58f', label: '已禁用' },
  deleted: { color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7', label: '已删除' },
};

export const FILE_STATUS: Record<string, StatusStyle> = {
  available: { color: '#52c41a', bg: '#f6ffed', border: '#b7eb8f', label: '可用' },
  deleted: { color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7', label: '已删除' },
};

export function statusOf(map: Record<string, StatusStyle>, key?: string | null): StatusStyle {
  if (!key) return DEFAULT_STATUS;
  return map[key] ?? DEFAULT_STATUS;
}

// ── role colors (global roles) ───────────────────────────────────────────────
const ROLE_COLORS: Record<string, string> = {
  super_admin: 'magenta',
  admin: 'red',
  operator: 'cyan',
  viewer: 'default',
};

export function roleColor(code: string): string {
  return ROLE_COLORS[code] ?? 'default';
}

// ── formatters ───────────────────────────────────────────────────────────────
export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function fmtDateTime(v?: string | null): string {
  if (!v) return '—';
  return new Date(v).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function fmtRelative(v?: string | null): string {
  if (!v) return '—';
  const t = new Date(v).getTime();
  const diff = Date.now() - t;
  if (diff < 0) return fmtDateTime(v);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} 天前`;
  return fmtDateTime(v);
}

/** Format a [0,1] confidence as a percentage string. Backend sends a Decimal. */
export function fmtConfidence(c?: number | null): string {
  if (c == null) return '—';
  return `${(c * 100).toFixed(0)}%`;
}

// ── StatusChip: a colored pill backed by a StatusStyle ───────────────────────
export function StatusChip({ style, text }: { style: StatusStyle; text?: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 10px',
        borderRadius: 6,
        fontSize: 12,
        fontWeight: 500,
        color: style.color,
        background: style.bg,
        border: `1px solid ${style.border}`,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: style.color,
          display: 'inline-block',
        }}
      />
      {text ?? style.label}
    </span>
  );
}

// ── PageHeader: consistent title + subtitle + actions row ────────────────────
export function PageHeader({
  title,
  subtitle,
  extra,
}: {
  title: string;
  subtitle?: string;
  extra?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div style={{ minWidth: 0 }}>
        <Typography.Title level={3} className="page-header-title">
          {title}
        </Typography.Title>
        {subtitle && (
          <Typography.Text type="secondary" className="page-header-subtitle">
            {subtitle}
          </Typography.Text>
        )}
      </div>
      {extra && <div style={{ flexShrink: 0 }}>{extra}</div>}
    </div>
  );
}

// ── StatCard: colored metric tile ────────────────────────────────────────────
export interface StatCardProps {
  label: string;
  value: ReactNode;
  icon: ReactNode;
  color: string;
  bg: string;
  hint?: string;
}

export function StatCard({ label, value, icon, color, bg, hint }: StatCardProps) {
  return (
    <Tooltip title={hint}>
      <div className="stat-card">
        <span className="stat-card-icon" style={{ background: bg, color }}>
          {icon}
        </span>
        <div style={{ minWidth: 0 }}>
          <div className="stat-card-value">{value}</div>
          <div className="stat-card-label">{label}</div>
        </div>
      </div>
    </Tooltip>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="stat-grid">{children}</div>;
}
