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
import {
  TransformComponent,
  TransformWrapper,
  type ReactZoomPanPinchRef,
} from 'react-zoom-pan-pinch';

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
  const transformRef = useRef<ReactZoomPanPinchRef | null>(null);
  const imageSizeRef = useRef<{ width: number; height: number } | null>(null);
  const resizeFrameRef = useRef<number | null>(null);

  const fitScale = useCallback(() => {
    const stage = stageRef.current;
    const imageSize = imageSizeRef.current;
    if (!stage || !imageSize?.width || !imageSize.height) return null;
    return Math.min(
      1,
      stage.clientWidth / imageSize.width,
      stage.clientHeight / imageSize.height,
    ) * 0.96;
  }, []);

  const fitView = useCallback((animationTime = 0) => {
    const scale = fitScale();
    if (scale !== null) transformRef.current?.centerView(scale, animationTime);
  }, [fitScale]);

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
    imageSizeRef.current = null;
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

  useEffect(() => {
    const stage = stageRef.current;
    if (!open || !objectUrl || !stage) return undefined;
    const observer = new ResizeObserver(() => {
      if (resizeFrameRef.current !== null) cancelAnimationFrame(resizeFrameRef.current);
      resizeFrameRef.current = requestAnimationFrame(() => {
        resizeFrameRef.current = null;
        fitView(0);
      });
    });
    observer.observe(stage);
    return () => {
      observer.disconnect();
      if (resizeFrameRef.current !== null) cancelAnimationFrame(resizeFrameRef.current);
      resizeFrameRef.current = null;
    };
  }, [fitView, objectUrl, open]);

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
          <div
            className="dxf-preview-stage"
            ref={stageRef}
            onDoubleClick={(event) => {
              if ((event.target as HTMLElement).closest('.dxf-preview-controls')) return;
              event.preventDefault();
              fitView(200);
            }}
          >
            <TransformWrapper
              ref={transformRef}
              initialScale={1}
              minScale={0.08}
              maxScale={24}
              limitToBounds={false}
              centerOnInit
              centerZoomedOut
              wheel={{ step: 0.12 }}
              doubleClick={{ disabled: true }}
              autoAlignment={{ disabled: true }}
            >
              {({ zoomIn, zoomOut }) => (
                <>
                  <div className="dxf-preview-controls">
                    <Tooltip title="放大">
                      <Button aria-label="放大预览" icon={<PlusOutlined />} onClick={() => zoomIn()} />
                    </Tooltip>
                    <Tooltip title="缩小">
                      <Button aria-label="缩小预览" icon={<MinusOutlined />} onClick={() => zoomOut()} />
                    </Tooltip>
                    <Tooltip title="适合窗口">
                      <Button aria-label="适合窗口" icon={<ScanOutlined />} onClick={() => fitView(200)} />
                    </Tooltip>
                  </div>
                  <TransformComponent
                    wrapperClass="dxf-preview-canvas"
                    contentClass="dxf-preview-content"
                  >
                    <img
                      src={objectUrl}
                      alt={`DXF 预览 ${fileName}`}
                      draggable={false}
                      onLoad={(event) => {
                        const image = event.currentTarget;
                        imageSizeRef.current = {
                          width: image.naturalWidth,
                          height: image.naturalHeight,
                        };
                        fitView(0);
                      }}
                    />
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
