import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App,
  Alert,
  Button,
  Modal,
  Space,
  Spin,
  Tag,
  Tooltip,
} from 'antd';
import {
  DownloadOutlined,
  FileImageOutlined,
  MinusOutlined,
  PlusOutlined,
  ReloadOutlined,
  ScanOutlined,
} from '@ant-design/icons';
import axios from 'axios';
import { TransformComponent, TransformWrapper } from 'react-zoom-pan-pinch';

import {
  downloadFile,
  fetchDxfPreview,
  fetchDxfPreviewBlob,
} from '../api/files.api';
import type { DxfPreviewResponse } from '../types/file';
import './DxfPreviewModal.css';

interface DxfPreviewModalProps {
  fileId: number | null;
  fileName: string;
  open: boolean;
  onClose: () => void;
}

function aciColor(index: number): string {
  return ({
    1: '#f87171',
    2: '#facc15',
    3: '#4ade80',
    4: '#22d3ee',
    5: '#60a5fa',
    6: '#e879f9',
    7: '#e5e7eb',
    8: '#94a3b8',
    9: '#cbd5e1',
  } as Record<number, string>)[Math.abs(index)] ?? '#a7bacb';
}

function previewError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const body = error.response?.data as { error?: { message?: string } } | undefined;
    if (body?.error?.message) return body.error.message;
  }
  return error instanceof Error ? error.message : 'DXF 预览加载失败';
}

export function DxfPreviewModal({
  fileId,
  fileName,
  open,
  onClose,
}: DxfPreviewModalProps) {
  const { message } = App.useApp();
  const [data, setData] = useState<DxfPreviewResponse | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const objectUrlRef = useRef<string | null>(null);

  const revokeObjectUrl = useCallback(() => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = null;
  }, []);

  const releaseObjectUrl = useCallback(() => {
    revokeObjectUrl();
    setObjectUrl(null);
  }, [revokeObjectUrl]);

  useEffect(() => {
    if (!open || fileId === null) {
      releaseObjectUrl();
      setData(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    let active = true;
    releaseObjectUrl();
    setData(null);
    setError(null);
    setLoading(true);

    void (async () => {
      try {
        const metadata = await fetchDxfPreview(fileId);
        const blob = await fetchDxfPreviewBlob(metadata.content_url, controller.signal);
        if (!active) return;
        const url = URL.createObjectURL(
          blob.type === metadata.content_type
            ? blob
            : new Blob([blob], { type: metadata.content_type }),
        );
        objectUrlRef.current = url;
        setObjectUrl(url);
        setData(metadata);
      } catch (loadError) {
        if (!controller.signal.aborted && active) setError(previewError(loadError));
      } finally {
        if (active) setLoading(false);
      }
    })();

    return () => {
      active = false;
      controller.abort();
    };
  }, [fileId, open, reloadKey, releaseObjectUrl]);

  useEffect(() => revokeObjectUrl, [revokeObjectUrl]);

  const handleDownload = useCallback(async () => {
    if (fileId === null) return;
    try {
      await downloadFile(fileId, fileName);
    } catch (downloadError) {
      message.error(previewError(downloadError));
    }
  }, [fileId, fileName, message]);

  const title = (
    <div className="dxf-preview-title">
      <span className="dxf-preview-title-mark"><FileImageOutlined /></span>
      <span className="dxf-preview-title-copy">
        <strong>DXF 在线预览</strong>
        <span>{fileName || '未命名图纸'}</span>
      </span>
      {data?.cached && <Tag color="cyan">已缓存</Tag>}
    </div>
  );

  return (
    <Modal
      className="dxf-preview-modal"
      title={title}
      open={open}
      width="min(1480px, 96vw)"
      centered
      destroyOnHidden
      onCancel={onClose}
      footer={(
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setReloadKey((key) => key + 1)}>
            重新加载
          </Button>
          <Button icon={<DownloadOutlined />} disabled={fileId === null} onClick={handleDownload}>
            下载源文件
          </Button>
          <Button type="primary" aria-label="关闭预览" onClick={onClose}>关闭</Button>
        </Space>
      )}
    >
      {loading && (
        <div className="dxf-preview-loading">
          <div style={{ textAlign: 'center' }}>
            <Spin size="large" />
            <div className="dxf-preview-loading-copy">正在检查缓存并构建图形记录…</div>
          </div>
        </div>
      )}

      {!loading && error && (
        <div className="dxf-preview-error">
          <Alert
            type="error"
            showIcon
            message="无法打开 DXF 预览"
            description={error}
            action={<Button onClick={() => setReloadKey((key) => key + 1)}>重试</Button>}
          />
        </div>
      )}

      {!loading && !error && data && objectUrl && (
        <div className="dxf-preview-shell">
          <div className="dxf-preview-stage">
            <TransformWrapper
              initialScale={1}
              minScale={0.08}
              maxScale={24}
              centerOnInit
              wheel={{ step: 0.12 }}
              doubleClick={{ mode: 'reset' }}
            >
              {({ zoomIn, zoomOut, resetTransform }) => (
                <>
                  <div className="dxf-preview-controls">
                    <Tooltip title="放大">
                      <Button aria-label="放大预览" icon={<PlusOutlined />} onClick={() => zoomIn()} />
                    </Tooltip>
                    <Tooltip title="缩小">
                      <Button aria-label="缩小预览" icon={<MinusOutlined />} onClick={() => zoomOut()} />
                    </Tooltip>
                    <Tooltip title="适合窗口">
                      <Button aria-label="重置预览" icon={<ScanOutlined />} onClick={() => resetTransform()} />
                    </Tooltip>
                  </div>
                  <TransformComponent
                    wrapperClass="dxf-preview-canvas"
                    contentStyle={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center' }}
                  >
                    <img src={objectUrl} alt={`DXF 预览 ${fileName}`} width={1200} height={900} draggable={false} />
                  </TransformComponent>
                </>
              )}
            </TransformWrapper>
            <div className="dxf-preview-status">
              <span>SVG / AUTHENTICATED</span>
              <span>{data.cached ? 'CACHE HIT' : 'NEW RENDER'}</span>
            </div>
          </div>

          <aside className="dxf-preview-sidebar" aria-label="DXF 图形信息">
            <div className="dxf-preview-kicker">Drawing telemetry</div>
            <div className="dxf-preview-metrics">
              <div className="dxf-preview-metric">
                <strong>{data.document_entities.toLocaleString('zh-CN')}</strong>
                <span>文档实体</span>
              </div>
              <div className="dxf-preview-metric">
                <strong>{data.modelspace_entities.toLocaleString('zh-CN')}</strong>
                <span>模型空间</span>
              </div>
              <div className="dxf-preview-metric">
                <strong>{data.layers.length.toLocaleString('zh-CN')}</strong>
                <span>图层</span>
              </div>
            </div>

            <section className="dxf-preview-section">
              <div className="dxf-preview-section-title">
                <span>实体构成</span><span>{Object.keys(data.entity_counts).length} 类</span>
              </div>
              <div className="dxf-preview-tags">
                {Object.entries(data.entity_counts)
                  .sort((left, right) => right[1] - left[1])
                  .map(([type, count]) => <Tag key={type}>{type} · {count}</Tag>)}
              </div>
            </section>

            <section className="dxf-preview-section">
              <div className="dxf-preview-section-title">
                <span>图层索引</span><span>ACI</span>
              </div>
              <div className="dxf-preview-layers">
                {data.layers.slice(0, 50).map((layer) => {
                  const color = data.layer_colors[layer] ?? 7;
                  return (
                    <div className="dxf-preview-layer" key={layer} title={layer}>
                      <i style={{ background: aciColor(color) }} />
                      <span>{layer}</span>
                      <code>{color}</code>
                    </div>
                  );
                })}
              </div>
            </section>
          </aside>
        </div>
      )}
    </Modal>
  );
}
