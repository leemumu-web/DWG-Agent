import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircleFilled, CloseCircleFilled, InboxOutlined, LoadingOutlined } from '@ant-design/icons';
import { Button, Progress, Typography } from 'antd';
import type { TransferProgress, TransferProgressHandler } from '../../shared/api';
import { completedTransferProgress, describeApiError, initialTransferProgress } from '../../shared/api';
import { TransferProgressBar } from '../../shared/components';
const { Text } = Typography;

// ── toast types ─────────────────────────────────────────────────────────────
interface Toast {
  id: number;
  name: string;
  done: boolean;
  error?: string;
  phase: 'enter' | 'visible' | 'exit';
}

const TOAST_TTL = 4000;
const MAX_VISIBLE = 3;

function fmtName(name: string, max = 30): string {
  if (name.length <= max) return name;
  const ext = name.lastIndexOf('.');
  const base = ext > 0 ? name.slice(0, ext) : name;
  const extn = ext > 0 ? name.slice(ext) : '';
  const keep = Math.max(4, max - extn.length - 1);
  return base.slice(0, keep) + '…' + extn;
}

let _nextId = 1;

export function FileUpload({ onUploaded, batchName, acceptExt = '.dwg', uploadFn, label, disabled = false, onBusyChange }: {
  onUploaded?: () => void;
  batchName?: string;
  acceptExt?: string;
  uploadFn: (
    file: File,
    batchName?: string,
    onProgress?: TransferProgressHandler,
  ) => Promise<unknown>;
  label?: string;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [active, setActive] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [transferProgress, setTransferProgress] = useState<TransferProgress | null>(null);
  const timerRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
  const inputRef = useRef<HTMLInputElement>(null);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, phase: 'exit' as const } : t)));
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 320);
  }, []);

  const addToast = useCallback(
    (name: string, error?: string) => {
      const id = _nextId++;
      setToasts((prev) => {
        const visible = prev.filter((t) => t.phase !== 'exit');
        if (visible.length >= MAX_VISIBLE) {
          const oldest = visible[0];
          setTimeout(() => dismiss(oldest.id), 0);
        }
        const toast: Toast = { id, name, done: true, error, phase: 'enter' };
        requestAnimationFrame(() => {
          setToasts((p) => p.map((t) => (t.id === id ? { ...t, phase: 'visible' } : t)));
        });
        return [...prev, toast];
      });
      if (!error) {
        const timer = setTimeout(() => dismiss(id), TOAST_TTL);
        timerRef.current.set(id, timer);
      }
    },
    [dismiss],
  );

  useEffect(() => {
    return () => { timerRef.current.forEach((t) => clearTimeout(t)); };
  }, []);

  const handleFiles = async (files: File[]) => {
    setActive(true);
    onBusyChange?.(true);
    const total = files.length;
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    const loadedByFile = new Map<File, number>();
    const updateTransfer = (file: File, loadedBytes: number) => {
      loadedByFile.set(file, Math.min(file.size, Math.max(0, loadedBytes)));
      const loaded = Array.from(loadedByFile.values()).reduce((sum, value) => sum + value, 0);
      setTransferProgress({
        loadedBytes: loaded,
        totalBytes,
        // 进度条封顶 99：全部完成后才由 completedTransferProgress 置 100，
        // 中间的 99 表示「仍在进行」，避免完成前误判。
        percent: totalBytes > 0 ? Math.min(99, Math.round((loaded / totalBytes) * 100)) : 100,
        completed: false,
        totalIsEstimated: false,
      });
    };
    setProgress({ done: 0, total });
    setTransferProgress(initialTransferProgress(totalBytes));

    let done = 0;
    const worker = async () => {
      while (true) {
        let file: File | undefined;
        // Use a simple counter since shift isn't safe across async workers
        const idx = done;
        if (idx >= total) break;
        file = files[idx];
        if (!file) break;
        done++;

        try {
          await uploadFn(file, batchName, (next) => {
            updateTransfer(file, next.loadedBytes);
          });
          updateTransfer(file, file.size);
          addToast(file.name);
        } catch (err: unknown) {
          updateTransfer(file, file.size);
          addToast(file.name, describeApiError(err, '上传失败'));
        }
        setProgress((p) => ({ ...p, done: p.done + 1 }));
      }
    };

    // 3 路并发上传：浏览器并发上限与带宽/服务器压力的权衡（刻意设计，
    // 勿改成串行或无限并发）。
    await Promise.all(
      Array.from({ length: Math.min(3, total) }, () => worker()),
    );

    setTransferProgress(completedTransferProgress(totalBytes, totalBytes));
    setActive(false);
    onBusyChange?.(false);
    onUploaded?.();
  };

  const handleClick = () => { if (!disabled && !active) inputRef.current?.click(); };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      const dwgFiles = Array.from(files).filter((f) =>
        f.name.toLowerCase().endsWith(acceptExt),
      );
      if (dwgFiles.length === 0) {
        addToast(files[0].name, `不支持的文件类型，仅接受 ${acceptExt}`);
      } else {
        handleFiles(dwgFiles);
      }
      e.target.value = '';
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={acceptExt}
        multiple
        style={{ display: 'none' }}
        disabled={disabled || active}
        onChange={handleInputChange}
      />

      <Button
        icon={active ? <LoadingOutlined /> : <InboxOutlined />}
        loading={active}
        disabled={disabled}
        onClick={handleClick}
        style={{
          borderColor: '#1677ff',
          color: '#1677ff',
          fontWeight: 500,
        }}
      >
        {active
          ? `正在上传 ${progress.done}/${progress.total} ...`
          : label || `上传 ${acceptExt.replace('.', '').toUpperCase()} 文件`}
      </Button>

      {active && progress.total > 1 && (
        <Progress
          percent={Math.round((progress.done / progress.total) * 100)}
          size="small"
          style={{ width: 120, display: 'inline-flex', marginLeft: 12 }}
          strokeColor="#1677ff"
        />
      )}
      {transferProgress && progress.total > 1 && (
        <TransferProgressBar label="图纸批量上传" progress={transferProgress} />
      )}

      {/* per-file toast stack */}
      <div className="upload-toast-stack" aria-live="polite" style={{ marginTop: toasts.length > 0 ? 8 : 0 }}>
        {toasts.map((t) => (
          <div
            key={t.id}
            title={t.error}
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
              {t.error?.includes('已上传') ? '待补交' : t.error ? '失败' : '已提交'}
            </Text>
          </div>
        ))}
      </div>
    </>
  );
}
