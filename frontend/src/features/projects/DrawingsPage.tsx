import { useMemo, useState } from 'react';
import {
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  ReloadOutlined,
  FileImageOutlined,
  SearchOutlined,
  EyeOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { listDrawings, listDrawingVersions, type DrawingVersion } from './drawings.api';
import { listProjects } from './projects.api';
import type { Drawing } from './drawing';
import {
  fmtDateTime,
  PageHeader,
  StatCard,
  StatGrid,
  StatusChip,
  statusOf,
} from '../../shared/components';

const DRAWING_STATUS: Record<string, { color: string; bg: string; border: string; label: string }> = {
  active: { color: '#52c41a', bg: '#f6ffed', border: '#b7eb8f', label: '活跃' },
  archived: { color: '#8c8c8c', bg: '#fafafa', border: '#f0f0f0', label: '已归档' },
  deleted: { color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7', label: '已删除' },
};

const DISCIPLINES: Record<string, { label: string; color: string }> = {
  architecture: { label: '建筑', color: 'blue' },
  structure: { label: '结构', color: 'geekblue' },
  mep: { label: '机电', color: 'purple' },
  hvac: { label: '暖通', color: 'cyan' },
  electrical: { label: '电气', color: 'gold' },
  plumbing: { label: '给排水', color: 'green' },
};
const disciplineTag = (d?: string | null) => {
  if (!d) return <Typography.Text type="secondary">—</Typography.Text>;
  const meta = DISCIPLINES[d] ?? { label: d, color: 'default' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
};

export function DrawingsPage() {
  const drawingsQ = useQuery({ queryKey: ['drawings'], queryFn: listDrawings });
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });

  const [search, setSearch] = useState('');
  const [disciplineFilter, setDisciplineFilter] = useState<string>('all');
  const [detail, setDetail] = useState<Drawing | null>(null);
  const [versions, setVersions] = useState<DrawingVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const projectMap = useMemo(() => {
    const m = new Map<number, { code: string; name: string }>();
    for (const p of projectsQ.data ?? []) m.set(p.id, { code: p.code, name: p.name });
    return m;
  }, [projectsQ.data]);

  const drawings = drawingsQ.data ?? [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return drawings.filter((d) => {
      if (disciplineFilter !== 'all' && (d.discipline ?? '') !== disciplineFilter) return false;
      if (!q) return true;
      return (
        (d.drawing_no ?? '').toLowerCase().includes(q) ||
        (d.title ?? '').toLowerCase().includes(q)
      );
    });
  }, [drawings, search, disciplineFilter]);

  const activeCount = drawings.filter((d) => d.status === 'active').length;
  const disciplines = useMemo(
    () => Array.from(new Set(drawings.map((d) => d.discipline).filter(Boolean))) as string[],
    [drawings],
  );

  async function loadVersions(d: Drawing) {
    setDetail(d);
    setVersionsLoading(true);
    try {
      setVersions(await listDrawingVersions(d.id));
    } catch {
      setVersions([]);
    }
    setVersionsLoading(false);
  }

  const columns = [
    { title: '#', dataIndex: 'id', width: 56, align: 'center' as const },
    {
      title: '图号', dataIndex: 'drawing_no', width: 140,
      render: (v?: string | null) => v ? <Typography.Text code>{v}</Typography.Text> : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '标题', dataIndex: 'title',
      render: (v?: string | null) => v ? <Typography.Text strong>{v}</Typography.Text> : <Typography.Text type="secondary">未命名</Typography.Text>,
    },
    {
      title: '所属项目', dataIndex: 'project_id', width: 180,
      render: (v: number) => {
        const p = projectMap.get(v);
        return p ? <Tooltip title={p.name}><Typography.Text>{p.code}</Typography.Text></Tooltip> : `#${v}`;
      },
    },
    {
      title: '专业', dataIndex: 'discipline', width: 100,
      render: (v?: string | null) => disciplineTag(v),
    },
    {
      title: '版本', dataIndex: 'current_version_id', width: 80, align: 'center' as const,
      render: (v?: number | null) => v ? <Tag>v{v}</Tag> : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (v: string) => <StatusChip style={statusOf(DRAWING_STATUS, v)} />,
    },
    {
      title: '操作', width: 80, align: 'center' as const,
      render: (_: unknown, r: Drawing) => (
        <Tooltip title="查看详情 / 版本">
          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => loadVersions(r)} />
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="图纸管理"
        subtitle="项目下的图纸与版本"
        extra={
          <Space>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索图号 / 标题"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 220 }}
            />
            <Button icon={<ReloadOutlined />} onClick={() => drawingsQ.refetch()} loading={drawingsQ.isFetching} />
          </Space>
        }
      />

      <StatGrid>
        <StatCard label="图纸总数" value={drawings.length} icon={<FileImageOutlined />} color="#1677ff" bg="#e6f4ff" />
        <StatCard label="活跃图纸" value={activeCount} icon={<FileImageOutlined />} color="#52c41a" bg="#f6ffed" />
        <StatCard label="涉及专业" value={disciplines.length} icon={<HistoryOutlined />} color="#722ed1" bg="#f9f0ff" />
      </StatGrid>

      {disciplines.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Segmented
            value={disciplineFilter}
            onChange={(v) => setDisciplineFilter(v as string)}
            options={[
              { label: '全部', value: 'all' },
              ...disciplines.map((d) => ({ label: DISCIPLINES[d]?.label ?? d, value: d })),
            ]}
          />
        </div>
      )}

      <Table
        className="surface-table"
        rowKey="id"
        dataSource={filtered}
        columns={columns}
        loading={drawingsQ.isLoading}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 张图纸` }}
        locale={{ emptyText: '暂无图纸' }}
        scroll={{ x: 900 }}
      />

      <Drawer
        title={detail ? `图纸 · ${detail.title || detail.drawing_no || '#' + detail.id}` : '图纸详情'}
        open={detail !== null}
        onClose={() => { setDetail(null); setVersions([]); }}
        width={520}
        loading={versionsLoading}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 20 }}>
              <Descriptions.Item label="图号">{detail.drawing_no || '—'}</Descriptions.Item>
              <Descriptions.Item label="标题">{detail.title || '—'}</Descriptions.Item>
              <Descriptions.Item label="所属项目">
                {(() => {
                  const p = projectMap.get(detail.project_id);
                  return p ? `${p.code} · ${p.name}` : `#${detail.project_id}`;
                })()}
              </Descriptions.Item>
              <Descriptions.Item label="专业">{disciplineTag(detail.discipline)}</Descriptions.Item>
              <Descriptions.Item label="状态"><StatusChip style={statusOf(DRAWING_STATUS, detail.status)} /></Descriptions.Item>
              <Descriptions.Item label="当前版本">{detail.current_version_id ? `v${detail.current_version_id}` : '—'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{fmtDateTime(detail.created_at)}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{fmtDateTime(detail.updated_at)}</Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5}><HistoryOutlined /> 版本历史 ({versions.length})</Typography.Title>
            {versions.length === 0 && !versionsLoading ? (
              <Empty description="暂无版本记录" />
            ) : (
              <Table
                rowKey="id"
                dataSource={versions}
                size="small"
                pagination={false}
                loading={versionsLoading}
                columns={[
                  { title: '版本', dataIndex: 'version_no', width: 70, render: (v: number) => <Tag color="blue">v{v}</Tag> },
                  { title: '来源', dataIndex: 'source', render: (v?: string | null) => v ?? '—' },
                  { title: '文件 ID', dataIndex: 'file_id', render: (v: number) => <Typography.Text code>#{v}</Typography.Text> },
                ]}
              />
            )}
          </>
        )}
      </Drawer>
    </>
  );
}
