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
import { TransformComponent, TransformWrapper } from 'react-zoom-pan-pinch';

import {
  downloadFile,
  fetchDxfPreview,
  fetchDxfPreviewBlob,
} from './files.api';
import { describeApiError } from '../../shared/api';
import type { DxfPreviewResponse } from './file';
import './DxfPreviewModal.css';

interface DxfPreviewModalProps {
  fileId: number | null;
  fileName: string;
  open: boolean;
  onClose: () => void;
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
  const stageRef = useRef<HTMLDivElement | null>(null);

  const fitScale = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return 1;
    return Math.min(1, stage.clientWidth / 1200, stage.clientHeight / 900) * 0.96;
  }, []);

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
        if (!controller.signal.aborted && active) setError(describeApiError(loadError, 'DXF 预览加载失败'));
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
      message.error(describeApiError(downloadError, 'DXF 预览加载失败'));
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
          <div className="dxf-preview-stage" ref={stageRef}>
            <TransformWrapper
              initialScale={1}
              minScale={0.08}
              maxScale={24}
              limitToBounds={false}
              centerOnInit
              centerZoomedOut
              wheel={{ step: 0.12 }}
              doubleClick={{ mode: 'reset' }}
              autoAlignment={{ disabled: true }}
              onInit={({ centerView }) => requestAnimationFrame(() => centerView(fitScale(), 0))}
            >
              {({ zoomIn, zoomOut, centerView }) => (
                <>
                  <div className="dxf-preview-controls">
                    <Tooltip title="放大">
                      <Button aria-label="放大预览" icon={<PlusOutlined />} onClick={() => zoomIn()} />
                    </Tooltip>
                    <Tooltip title="缩小">
                      <Button aria-label="缩小预览" icon={<MinusOutlined />} onClick={() => zoomOut()} />
                    </Tooltip>
                    <Tooltip title="适合窗口">
                      <Button aria-label="适合窗口" icon={<ScanOutlined />} onClick={() => centerView(fitScale())} />
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
          </div>
        </div>
      )}
    </Modal>
  );
}
