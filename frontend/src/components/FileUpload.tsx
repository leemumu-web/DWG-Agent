import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CheckCircleFilled,
  CloseCircleFilled,
  CloudUploadOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { Progress, Typography, Upload, type UploadProps } from 'antd';
import { uploadDwgAndConvert } from '../api/files.api';

const { Text, Paragraph } = Typography;

// ── toast types ─────────────────────────────────────────────────────────────
interface Toast {
  id: number;
  name: string;
  done: boolean;
  error?: string;
  /** 'enter' → visible, 'exit' → sliding out (wait for animation), null → remove */
  phase: 'enter' | 'visible' | 'exit';
}

const TOAST_TTL = 4000;   // visible duration before auto-dismiss
const MAX_VISIBLE = 3;

// ── helpers ─────────────────────────────────────────────────────────────────
function fmtName(name: string, max = 30): string {
  if (name.length <= max) return name;
  const ext = name.lastIndexOf('.');
  const base = ext > 0 ? name.slice(0, ext) : name;
  const extn = ext > 0 ? name.slice(ext) : '';
  const keep = Math.max(4, max - extn.length - 1);
  return base.slice(0, keep) + '…' + extn;
}

let _nextId = 1;

// ── component ───────────────────────────────────────────────────────────────

/**
 * DWG upload + auto-convert trigger with animated toast feedback.
 *
 * Uses Upload.Dragger for drag-and-drop + click, accept='.dwg', multiple.
 * Each file is uploaded then immediately enqueues a convert_dwg_to_dxf job.
 *
 * Feedback is rendered as a compact toast stack (max 3 visible) with
 * slide-in / slide-out CSS animations — no global message spam.
 */
export function FileUpload({ onUploaded, batchName }: { onUploaded?: () => void; batchName?: string }) {
  const [active, setActive] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timerRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  // ── toast helpers ────────────────────────────────────────────────────────
  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, phase: 'exit' as const } : t)));
    // remove from DOM after exit animation (300ms)
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 320);
  }, []);

  const addToast = useCallback(
    (name: string, error?: string) => {
      const id = _nextId++;
      setToasts((prev) => {
        const visible = prev.filter((t) => t.phase !== 'exit');
        // If we already have MAX_VISIBLE, dismiss the oldest
        if (visible.length >= MAX_VISIBLE) {
          const oldest = visible[0];
          setTimeout(() => dismiss(oldest.id), 0);
        }
        const toast: Toast = { id, name, done: true, error, phase: 'enter' };
        // After mount, switch phase → visible so CSS transition triggers
        requestAnimationFrame(() => {
          setToasts((p) => p.map((t) => (t.id === id ? { ...t, phase: 'visible' } : t)));
        });
        return [...prev, toast];
      });

      // Auto-dismiss on success after TTL
      if (!error) {
        const timer = setTimeout(() => dismiss(id), TOAST_TTL);
        timerRef.current.set(id, timer);
      }
    },
    [dismiss],
  );

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      timerRef.current.forEach((t) => clearTimeout(t));
    };
  }, []);

  // ── upload logic (concurrent, max 3 at a time) ──────────────────────────
  const handleFiles = async (files: File[]) => {
    setActive(true);
    const total = files.length;
    let done = 0;

    const uploadOne = async (file: File, idx: number) => {
      try {
        await uploadDwgAndConvert(file, batchName);
        addToast(file.name);
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast(file.name, msg || '上传失败');
      } finally {
        done++;
      }
    };

    // Simple concurrency pool: at most 3 uploads in flight
    const concurrency = Math.min(3, total);
    const queue = files.slice();
    const workers = Array.from({ length: concurrency }, async () => {
      while (queue.length > 0) {
        const file = queue.shift()!;
        await uploadOne(file, total - queue.length - 1);
      }
    });
    await Promise.all(workers);

    setActive(false);
    onUploaded?.();
  };

  const uploadProps: UploadProps = {
    accept: '.dwg',
    multiple: true,
    showUploadList: false,
    disabled: active,
    beforeUpload: (file) => {
      if (!file.name.toLowerCase().endsWith('.dwg')) {
        addToast(file.name, '不支持的文件类型，仅接受 .dwg');
        return Upload.LIST_IGNORE;
      }
      return false;
    },
    fileList: [],
    onChange: (info) => {
      const files = info.fileList
        .map((f) => f.originFileObj)
        .filter((f): f is NonNullable<typeof f> => !!f) as File[];
      if (files.length > 0 && !active) {
        void handleFiles(files);
      }
    },
  };

  // ── render ────────────────────────────────────────────────────────────────
  const totalCount = toasts.filter((t) => t.phase !== 'exit').length;
  const successCount = toasts.filter((t) => !t.error && t.phase !== 'exit').length;
  const overallPercent = totalCount > 0 ? Math.round((successCount / totalCount) * 100) : 0;

  return (
    <div style={{ marginBottom: 20 }}>
      <Upload.Dragger {...uploadProps} style={{ padding: '24px 16px' }}>
        {active ? (
          <div className="upload-progress">
            <LoadingOutlined style={{ fontSize: 40, color: '#1677ff' }} />
            <Paragraph style={{ fontSize: 15, fontWeight: 500, color: '#1f1f1f', margin: '12px 0 4px' }}>
              {totalCount > 0 ? `已处理 ${successCount}/${totalCount}` : '正在上传…'}
            </Paragraph>
            {totalCount > 0 && (
              <Progress
                percent={overallPercent}
                status="active"
                strokeColor="#1677ff"
                style={{ maxWidth: 320, margin: '4px auto 0' }}
              />
            )}
            <Text type="secondary" style={{ fontSize: 13, display: 'block', marginTop: 8 }}>
              上传完成后将自动开始 DWG→DXF 转换
            </Text>
          </div>
        ) : (
          <div className="upload-idle">
            <CloudUploadOutlined className="upload-icon" />
            <Paragraph style={{ fontSize: 16, fontWeight: 500, color: '#1f1f1f', margin: '12px 0 6px' }}>
              点击或拖拽 DWG 文件到此区域上传
            </Paragraph>
            <Text type="secondary" style={{ fontSize: 13 }}>
              支持批量上传 · 自动转换为 DXF R2018 格式 · 单文件最大 512 MB
            </Text>
          </div>
        )}
      </Upload.Dragger>

      {/* ── animated toast stack ─────────────────────────────────────── */}
      <div className="upload-toast-stack" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`upload-toast ${t.phase === 'exit' ? 'toast-exit' : t.phase === 'visible' ? 'toast-visible' : 'toast-enter'}`}
            style={{
              background: t.error ? '#fff2f0' : '#f6ffed',
              border: `1px solid ${t.error ? '#ffccc7' : '#b7eb8f'}`,
            }}
          >
            <span className="toast-icon">
              {t.error ? (
                <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 16 }} />
              ) : (
                <CheckCircleFilled style={{ color: '#52c41a', fontSize: 16 }} />
              )}
            </span>
            <Text className="toast-name" ellipsis style={{ flex: 1, fontSize: 13 }}>
              {fmtName(t.name)}
            </Text>
            <Text
              className="toast-status"
              style={{ fontSize: 12, color: t.error ? '#ff4d4f' : '#52c41a', marginLeft: 8, whiteSpace: 'nowrap' }}
            >
              {t.error ? '失败' : '已提交'}
            </Text>
          </div>
        ))}
      </div>
    </div>
  );
}
