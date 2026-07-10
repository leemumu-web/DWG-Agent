import { useState, useCallback, useEffect, useRef, type FC } from 'react';
import {
  Modal, Space, Typography, Badge, Button, Skeleton, Empty,
  message, Tag, Descriptions, Divider,
} from 'antd';
import {
  FileImageOutlined, ReloadOutlined, DownloadOutlined,
  EyeOutlined, ExpandOutlined,
} from '@ant-design/icons';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import { fetchDxfPreview, downloadFile } from '../api/files.api';
import type { DxfPreviewResponse } from '../types/file';

const { Text, Title } = Typography;

interface DxfPreviewModalProps {
  fileId: number | null;
  fileName?: string;
  open: boolean;
  onClose: () => void;
}

/** ACI color index → CSS fallback color for layer badges */
function aciToCss(aci: number): string {
  const table: Record<number, string> = {
    1: '#ff0000', 2: '#ffff00', 3: '#00ff00', 4: '#00ffff',
    5: '#0000ff', 6: '#ff00ff', 7: '#ffffff', 8: '#808080',
    9: '#c0c0c0',
  };
  return table[aci] || '#cccccc';
}

export const DxfPreviewModal: FC<DxfPreviewModalProps> = ({ fileId, fileName, open, onClose }) => {
  const [data, setData] = useState<DxfPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const loadedFileId = useRef<number | null>(null);

  useEffect(() => {
    if (!open) { setData(null); loadedFileId.current = null; }
  }, [open]);

  const load = useCallback(async (fid: number) => {
    setLoading(true);
    try {
      const result = await fetchDxfPreview(fid);
      setData(result);
      loadedFileId.current = fid;
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载预览失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && fileId !== null && loadedFileId.current !== fileId) {
      setData(null);
      load(fileId);
    }
  }, [open, fileId, load]);

  const handleDownload = useCallback(async () => {
    if (fileId === null || !fileName) return;
    try { await downloadFile(fileId, fileName); }
    catch (err) { message.error(err instanceof Error ? err.message : '下载失败'); }
  }, [fileId, fileName]);

  const handleRefresh = useCallback(() => {
    if (fileId !== null) load(fileId);
  }, [fileId, load]);

  const entityCount = data?.total_entities || 0;
  const layerCount = data?.layers.length || 0;

  // Build a full download URL from the backend-relative path
  const previewUrl = data?.preview_url
    ? `${import.meta.env.VITE_API_BASE_URL || ''}${data.preview_url}`
    : null;

  return (
    <Modal
      title={
        <Space size={12}>
          <FileImageOutlined style={{ color: '#1677ff', fontSize: 16 }} />
          <Text strong style={{ fontSize: 15 }}>{fileName || 'DXF 预览'}</Text>
          {entityCount > 0 && (
            <Badge count={`${entityCount.toLocaleString()} 实体`}
              style={{ backgroundColor: '#1677ff' }} />
          )}
          {layerCount > 0 && (
            <Badge count={`${layerCount} 图层`}
              style={{ backgroundColor: '#722ed1' }} />
          )}
          {data?.cached && (
            <Tag color="green" style={{ margin: 0 }}>缓存</Tag>
          )}
        </Space>
      }
      open={open}
      onCancel={onClose}
      width="94vw"
      style={{ top: 12 }}
      styles={{
        body: { padding: 0, height: 'calc(94vh - 110px)', overflow: 'hidden' },
      }}
      footer={
        <Space>
          <Button icon={<ReloadOutlined spin={loading} />} onClick={handleRefresh}
            disabled={loading} size="middle">刷新</Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={handleDownload} size="middle">
            下载 {fileName || 'DXF'}
          </Button>
          <Button onClick={onClose} size="middle">关闭</Button>
        </Space>
      }
      destroyOnClose
    >
      {loading && (
        <div style={{ padding: 24, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Skeleton active paragraph={{ rows: 8 }} style={{ width: '60%' }} />
        </div>
      )}

      {!loading && data && (
        <div style={{ display: 'flex', height: '100%' }}>
          {/* ── Image viewer (main area) ──────────────────────────── */}
          <div style={{ flex: 1, background: '#0d0d1a', position: 'relative', overflow: 'hidden' }}>
            {previewUrl ? (
              <TransformWrapper
                initialScale={1}
                minScale={0.05}
                maxScale={30}
                wheel={{ step: 0.1 }}
                centerOnInit
                doubleClick={{ mode: 'reset' }}
              >
                {({ zoomIn, zoomOut, resetTransform }) => (
                  <>
                    {/* Zoom controls */}
                    <div style={{
                      position: 'absolute', top: 12, right: 12, zIndex: 20,
                      display: 'flex', flexDirection: 'column', gap: 4,
                    }}>
                      <Button size="small" icon={<ExpandOutlined />}
                        onClick={() => zoomIn()} title="放大" />
                      <Button size="small" icon={<EyeOutlined />}
                        onClick={() => resetTransform()} title="重置" />
                    </div>

                    <TransformComponent
                      wrapperStyle={{ width: '100%', height: '100%' }}
                      contentStyle={{ width: '100%', height: '100%',
                        display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    >
                      <img
                        src={previewUrl}
                        alt={`DXF preview: ${fileName}`}
                        style={{
                          maxWidth: 'none', maxHeight: 'none',
                          display: 'block',
                        }}
                      />
                    </TransformComponent>
                  </>
                )}
              </TransformWrapper>
            ) : (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: '100%', color: '#666',
              }}>
                <Empty description="无法加载预览图片" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              </div>
            )}

            {/* Coordinate hint */}
            <div style={{
              position: 'absolute', bottom: 8, left: 8, zIndex: 20,
              background: 'rgba(0,0,0,0.65)', borderRadius: 4,
              padding: '2px 10px',
            }}>
              <Text style={{ color: '#999', fontSize: 11, fontFamily: 'monospace' }}>
                {data.bounds
                  ? `${(data.bounds.max_x - data.bounds.min_x).toFixed(0)} × ${(data.bounds.max_y - data.bounds.min_y).toFixed(0)} mm`
                  : ''}
              </Text>
            </div>
          </div>

          {/* ── Sidebar (metadata) ───────────────────────────────── */}
          <div style={{
            width: 280, borderLeft: '1px solid #f0f0f0',
            overflowY: 'auto', padding: 16, background: '#fafafa',
          }}>
            <Title level={5} style={{ marginTop: 0 }}>实体统计</Title>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="总实体数">
                {entityCount.toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="X 范围">
                {data.bounds.min_x.toFixed(1)} – {data.bounds.max_x.toFixed(1)}
              </Descriptions.Item>
              <Descriptions.Item label="Y 范围">
                {data.bounds.min_y.toFixed(1)} – {data.bounds.max_y.toFixed(1)}
              </Descriptions.Item>
            </Descriptions>

            {Object.keys(data.entity_counts).length > 0 && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {Object.entries(data.entity_counts)
                    .sort(([, a], [, b]) => b - a)
                    .map(([type, count]) => (
                      <Tag key={type} style={{ margin: 0, fontSize: 12 }}>
                        {type}: {count}
                      </Tag>
                    ))}
                </div>
              </>
            )}

            <Divider style={{ margin: '12px 0' }} />
            <Title level={5} style={{ marginTop: 0 }}>
              图层 ({layerCount})
            </Title>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {data.layers.slice(0, 30).map((layer) => {
                const aci = data.layer_colors[layer] || 7;
                return (
                  <div key={layer} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '2px 0',
                  }}>
                    <span style={{
                      width: 12, height: 12, borderRadius: 2,
                      background: aciToCss(aci),
                      border: '1px solid #d9d9d9', flexShrink: 0,
                    }} />
                    <Text style={{ fontSize: 12 }} ellipsis={{ tooltip: layer }}>
                      {layer}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 11, marginLeft: 'auto' }}>
                      ACI {aci}
                    </Text>
                  </div>
                );
              })}
              {data.layers.length > 30 && (
                <Text type="secondary" style={{ fontSize: 11, marginTop: 4 }}>
                  … 还有 {data.layers.length - 30} 个图层
                </Text>
              )}
            </div>
          </div>
        </div>
      )}

      {!loading && !data && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          height: '100%',
        }}>
          <Empty description="无法加载预览数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      )}
    </Modal>
  );
};
