import { Alert, Skeleton, Tag } from 'antd';
import {
  AppstoreOutlined,
  BuildOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';

import type { ExcelFinalHealth, ExcelFinalOverview as Overview } from '../types';

interface ExcelFinalOverviewProps {
  health?: ExcelFinalHealth;
  overview?: Overview;
  loading: boolean;
  error?: string;
}

function format(value: number, digits = 0): string {
  return value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

const DEGRADED_LABELS: Record<string, string> = {
  pipeline_disabled: '处理管道未启用',
  stage: 'Excel 处理程序不可用',
  dependencies: '处理依赖不完整',
  handbook_module: '五金手册模块不可用',
  handbook_database: '五金手册数据库不可用',
  database: '业务数据库不可用',
  object_storage: '对象存储不可用',
};

function databaseLabel(backend: string | undefined): string {
  return backend ? '业务数据库' : '数据库';
}

function storageLabel(backend: ExcelFinalHealth['storage_backend'] | undefined): string {
  if (backend === 'local') return '本机文件存储';
  return '文件存储';
}

export function ExcelFinalOverview({ health, overview, loading, error }: ExcelFinalOverviewProps) {
  if (loading && !overview) return <Skeleton active paragraph={{ rows: 2 }} />;

  const cards = [
    { label: '入库批次', value: format(overview?.batch_count ?? 0), icon: <DatabaseOutlined />, tone: 'cyan' },
    { label: '零件记录', value: format(overview?.part_count ?? 0), icon: <AppstoreOutlined />, tone: 'green' },
    { label: '构件汇总', value: format(overview?.component_count ?? 0), icon: <BuildOutlined />, tone: 'violet' },
    { label: '净重合计 / kg', value: format(overview?.total_net_weight ?? 0, 2), icon: <ExperimentOutlined />, tone: 'amber' },
  ];
  const degraded = health?.degraded_components ?? [];
  const degradedText = degraded
    .map((component) => DEGRADED_LABELS[component] ?? component)
    .join('、');

  return (
    <section className="excel-final-overview" aria-label="Excel Final 数据概览">
      <div className="excel-final-health-row">
        <div>
          <span className={`excel-final-health-dot ${health?.ready ? 'is-ready' : 'is-warning'}`} />
          <strong>{health?.ready ? '数据管道就绪' : '数据管道需要检查'}</strong>
          <span>
            {databaseLabel(health?.database_backend)} 权威数据 · {storageLabel(health?.storage_backend)} · 异步处理流水
          </span>
        </div>
        <Tag color={health?.ready ? 'success' : 'warning'}>
          {health?.ready ? '运行正常' : '需要检查'}
        </Tag>
      </div>
      {error && <Alert type="error" showIcon message="概览加载失败" description={error} />}
      {!health?.ready && health && (
        <Alert
          type="warning"
          showIcon
          message="Excel Final 处理链未完全就绪"
          description={degradedText
            ? `异常环节：${degradedText}。历史批次和已登记数据库记录仍可浏览。`
            : '处理链状态不完整；历史批次和已登记数据库记录仍可浏览。'}
        />
      )}
      <div className="excel-final-metric-grid">
        {cards.map((card) => (
          <article className={`excel-final-metric is-${card.tone}`} key={card.label}>
            <span className="excel-final-metric-icon" aria-hidden="true">{card.icon}</span>
            <div>
              <strong>{card.value}</strong>
              <span>{card.label}</span>
            </div>
          </article>
        ))}
      </div>
      <div className="excel-final-overview-foot">
        <span>毛重合计 <strong>{format(overview?.total_gross_weight ?? 0, 2)} kg</strong></span>
        <span>最近入库 <strong>{overview?.latest_created_at ? new Date(overview.latest_created_at).toLocaleString('zh-CN') : '暂无'}</strong></span>
      </div>
    </section>
  );
}
