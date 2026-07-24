import { useMemo, useState } from 'react';
import { App, Button, Card, Empty, Space, Table, Tabs, Typography } from 'antd';
import { DatabaseOutlined, EyeOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { DxfPreviewModal } from '../files';
import { useAuthStore } from '../../shared/auth';
import {
  downloadOriginal,
  archiveRemnant,
  getRemnant,
  listRemnantMaterials,
  markRemnantUsed,
  releaseRemnant,
  reserveRemnant,
  searchRemnants,
  cancelRemnantImportBatch,
  retryRemnantImportItem,
} from './api';
import { RemnantBatchProgress } from './RemnantBatchProgress';
import { RemnantConfirmationPanel } from './RemnantConfirmationPanel';
import { RemnantAutoImportPanel } from './RemnantAutoImportPanel';
import { RemnantImportPanel } from './RemnantImportPanel';
import { RemnantGlobalPanel } from './RemnantGlobalPanel';
import { RemnantMaterialCatalog } from './RemnantMaterialCatalog';
import { RemnantEditModal } from './RemnantEditModal';
import { RemnantDetailDrawer, StatusTag } from './RemnantDetailDrawer';
import { RemnantSearchPanel } from './RemnantSearchPanel';
import { useRemnantBatch } from './useRemnantBatch';
import type { Remnant, RemnantSearch, RemnantStatus } from './types';
import { describeRemnantError, describeRemnantErrorAsync } from './errors';
import './styles.css';

const activeStatuses: RemnantStatus[] = ['available', 'reserved'];

function fromParams(params: URLSearchParams): RemnantSearch {
  const statuses = params.getAll('status').filter(Boolean) as RemnantStatus[];
  return {
    materialId: params.get('material') ? Number(params.get('material')) : undefined,
    thicknessMm: params.get('thickness') ?? undefined,
    includeFamily: params.get('family') === '1',
    statuses: statuses.length ? statuses : activeStatuses,
    page: Math.max(1, Number(params.get('page') ?? 1)),
  };
}

export function RemnantInventoryPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => fromParams(params), [params]);
  const activeTab = ['global', 'import', 'auto', 'materials'].includes(params.get('tab') ?? '') ? params.get('tab')! : 'search';
  const batchId = params.get('batch') ? Number(params.get('batch')) : undefined;
  const [selectedId, setSelectedId] = useState<number>();
  const [preview, setPreview] = useState<{ id: number; name: string }>();
  const [editOpen, setEditOpen] = useState(false);
  const [downloadError, setDownloadError] = useState<string>();
  const [showInactiveMaterials, setShowInactiveMaterials] = useState(false);
  const user = useAuthStore((state) => state.user);
  const isAdmin = user?.roles.some((role) => ['admin', 'super_admin'].includes(role.code)) ?? false;

  const materials = useQuery({
    queryKey: ['remnant-materials'],
    queryFn: () => listRemnantMaterials(),
  });
  const materialCatalog = useQuery({
    queryKey: ['remnant-materials', 'catalog', showInactiveMaterials],
    queryFn: () => listRemnantMaterials(!showInactiveMaterials),
    enabled: activeTab === 'materials',
  });
  const results = useQuery({
    queryKey: ['remnants', search],
    queryFn: () => searchRemnants(search),
    enabled: Boolean(search.materialId && search.thicknessMm),
  });
  const detail = useQuery({
    queryKey: ['remnant', selectedId],
    queryFn: () => getRemnant(selectedId!),
    enabled: selectedId !== undefined,
  });
  const batch = useRemnantBatch(batchId);
  const batchAction = useMutation({
    mutationFn: async ({ kind, itemId }: { kind: 'retry' | 'cancel'; itemId?: number }) => {
      if (kind === 'retry') await retryRemnantImportItem(itemId!);
      else if (batchId) await cancelRemnantImportBatch(batchId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['remnant-import-batch', batchId] });
      message.success('批次状态已更新');
    },
    onError: (error) => message.error(describeRemnantError(error, '批次操作失败')),
  });

  const refresh = async (row?: Remnant) => {
    await queryClient.invalidateQueries({ queryKey: ['remnants'] });
    if (row) queryClient.setQueryData(['remnant', row.id], row);
  };
  const action = useMutation({
    mutationFn: async ({ kind, row }: { kind: 'reserve' | 'release' | 'used' | 'archive'; row: Remnant }) => {
      if (kind === 'reserve') return reserveRemnant(row);
      if (kind === 'release') return releaseRemnant(row.id);
      if (kind === 'archive') return archiveRemnant(row.id);
      return markRemnantUsed(row.id);
    },
    onSuccess: async (row) => {
      await refresh(row);
      message.success('库存状态已更新');
    },
    onError: async (error) => {
      await refresh();
      message.error(describeRemnantError(error, '操作未完成，库存可能已被其他工人更新'));
    },
  });
  const originalDownload = useMutation({
    mutationFn: downloadOriginal,
  });
  const handleOriginalDownload = async () => {
    if (!detail.data) return;
    setDownloadError(undefined);
    try {
      await originalDownload.mutateAsync(detail.data.id);
    } catch (error) {
      const description = await describeRemnantErrorAsync(error, '原图下载失败');
      setDownloadError(description);
      message.error(description);
    }
  };

  const canDownload = Boolean(detail.data && detail.data.status === 'reserved'
    && (isAdmin || detail.data.reserved_by === user?.id));

  const updateSearch = (next: RemnantSearch) => {
    const target = new URLSearchParams();
    if (next.materialId) target.set('material', String(next.materialId));
    if (next.thicknessMm) target.set('thickness', next.thicknessMm);
    if (next.includeFamily) target.set('family', '1');
    next.statuses.forEach((status) => target.append('status', status));
    if (next.page > 1) target.set('page', String(next.page));
    setParams(target);
  };

  return (
    <section className="remnant-page">
      <header className="remnant-page-header">
        <div className="remnant-page-icon"><DatabaseOutlined /></div>
        <div>
          <Typography.Title level={2}>余料库</Typography.Title>
          <Typography.Text type="secondary">按材质与厚度查找、预览和预占全厂共享余料。</Typography.Text>
        </div>
      </header>
      <Tabs
        activeKey={activeTab}
        onChange={(tab) => {
          const target = new URLSearchParams(params);
          if (tab === 'search') target.delete('tab');
          else target.set('tab', tab);
          if (!['import', 'auto'].includes(tab)) target.delete('batch');
          setParams(target);
        }}
        items={[
          { key: 'search', label: '余料检索', children: <div className="remnant-tab-stack">
      <RemnantSearchPanel materials={materials.data ?? []} loading={materials.isLoading} value={search} onSearch={updateSearch} />
      <Card className="remnant-results-card" bordered={false}>
        <div className="remnant-section-heading">
          <div>
            <Typography.Title level={4}>检索结果</Typography.Title>
            <Typography.Text type="secondary">
              {results.data ? `共 ${results.data.pagination.total} 张余料` : '填写查询条件后显示库存'}
            </Typography.Text>
          </div>
        </div>
        <Table<Remnant>
          rowKey="id"
          loading={results.isFetching}
          dataSource={results.data?.data ?? []}
          locale={{ emptyText: <Empty description="暂无符合条件的余料" /> }}
          pagination={{
            current: search.page,
            pageSize: 20,
            total: results.data?.pagination.total ?? 0,
            showSizeChanger: false,
            onChange: (page) => updateSearch({ ...search, page }),
          }}
          columns={[
            { title: '状态', dataIndex: 'status', width: 100, render: (status) => <StatusTag status={status} /> },
            { title: '材质', dataIndex: 'material_code', width: 120 },
            { title: '厚度', dataIndex: 'thickness_mm', width: 110, render: (value) => `${value} mm` },
            { title: '项目编号', dataIndex: 'project_no', ellipsis: true },
            { title: '零件', dataIndex: 'parts', ellipsis: true, render: (parts: string[]) => parts.join('、') },
            { title: '占用人', dataIndex: 'reserved_by_name', width: 130, render: (name, row) => name ?? (row.reserved_by ? `用户 #${row.reserved_by}` : '—') },
            { title: '原图', dataIndex: 'source_ext', width: 90, render: (ext) => ext.slice(1).toUpperCase() },
            {
              title: '操作', key: 'actions', width: 120,
              render: (_, row) => <Space><Button type="link" icon={<EyeOutlined />} onClick={() => setSelectedId(row.id)}>详情</Button></Space>,
            },
          ]}
        />
      </Card>
          </div> },
          { key: 'global', label: '全部余料', children: <RemnantGlobalPanel materials={materials.data ?? []} currentUserId={user?.id} isAdmin={isAdmin} onOpenDetail={setSelectedId} /> },
          { key: 'import', label: '批量导入', children: <div className="remnant-tab-stack">
            <RemnantImportPanel onCreated={(created) => {
              const target = new URLSearchParams(params);
              target.set('tab', 'import'); target.set('batch', String(created.id)); setParams(target);
            }} />
            {batch.data && <>
              <RemnantBatchProgress
                batch={batch.data}
                loading={batch.isFetching || batchAction.isPending}
                onRetry={(item) => batchAction.mutate({ kind: 'retry', itemId: item.id })}
                onCancel={() => batchAction.mutate({ kind: 'cancel' })}
              />
              {batch.data.pending_count > 0 && <RemnantConfirmationPanel batch={batch.data} materials={materials.data ?? []} />}
            </>}
          </div> },
          { key: 'auto', label: '自动导入', children: <div className="remnant-tab-stack">
            <RemnantAutoImportPanel onCreated={(created) => {
              const target = new URLSearchParams(params);
              target.set('tab', 'auto'); target.set('batch', String(created.id)); setParams(target);
            }} />
            {batch.data && <>
              <RemnantBatchProgress
                batch={batch.data}
                loading={batch.isFetching || batchAction.isPending}
                onRetry={(item) => batchAction.mutate({ kind: 'retry', itemId: item.id })}
                onCancel={() => batchAction.mutate({ kind: 'cancel' })}
              />
              <RemnantConfirmationPanel batch={batch.data} materials={materials.data ?? []} />
            </>}
          </div> },
          { key: 'materials', label: '材质管理', children: <RemnantMaterialCatalog
            materials={materialCatalog.data ?? []}
            loading={materialCatalog.isLoading}
            isAdmin={isAdmin}
            showInactive={showInactiveMaterials}
            onShowInactiveChange={setShowInactiveMaterials}
          /> },
        ]}
      />
      <RemnantDetailDrawer
        open={selectedId !== undefined}
        remnant={detail.data}
        canDownload={canDownload}
        canManage={Boolean(detail.data && detail.data.status === 'available' && (isAdmin || detail.data.imported_by === user?.id))}
        actionLoading={action.isPending}
        downloadLoading={originalDownload.isPending}
        downloadError={downloadError}
        onClose={() => { setSelectedId(undefined); setDownloadError(undefined); }}
        onPreview={() => detail.data && setPreview({ id: detail.data.dxf_file_id, name: detail.data.source_name })}
        onDownload={() => void handleOriginalDownload()}
        onReserve={() => detail.data && action.mutate({ kind: 'reserve', row: detail.data })}
        onRelease={() => detail.data && action.mutate({ kind: 'release', row: detail.data })}
        onMarkUsed={() => detail.data && action.mutate({ kind: 'used', row: detail.data })}
        onEdit={() => setEditOpen(true)}
        onArchive={() => detail.data && action.mutate({ kind: 'archive', row: detail.data })}
      />
      <RemnantEditModal
        open={editOpen}
        remnant={detail.data}
        materials={materials.data ?? []}
        onClose={() => setEditOpen(false)}
        onSaved={(row) => { setEditOpen(false); void refresh(row); message.success('余料信息已更新'); }}
      />
      <DxfPreviewModal
        fileId={preview?.id ?? null}
        fileName={preview?.name ?? ''}
        open={preview !== undefined}
        onClose={() => setPreview(undefined)}
      />
    </section>
  );
}
