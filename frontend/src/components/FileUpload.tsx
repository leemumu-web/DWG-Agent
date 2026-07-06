import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircleFilled, CloseCircleFilled, InboxOutlined, LoadingOutlined } from '@ant-design/icons';
import { Button, Progress, Typography } from 'antd';
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

export function FileUpload({ onUploaded, batchName, acceptExt = '.dwg', uploadFn, label }: {
  onUploaded?: () => void;
  batchName?: string;
  acceptExt?: string;
  uploadFn: (file: File, batchName?: string) => Promise<unknown>;
  label?: string;
}) {
  const [active, setActive] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
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
    const total = files.length;
    setProgress({ done: 0, total });

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
          await uploadFn(file, batchName);
          addToast(file.name);
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : String(err);
          addToast(file.name, msg || '上传失败');
        }
        setProgress((p) => ({ ...p, done: p.done + 1 }));
      }
    };

    await Promise.all(
      Array.from({ length: Math.min(3, total) }, () => worker()),
    );

    setActive(false);
    onUploaded?.();
  };

  const handleClick = () => inputRef.current?.click();

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
        onChange={handleInputChange}
      />

      <Button
        icon={active ? <LoadingOutlined /> : <InboxOutlined />}
        loading={active}
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

      {/* per-file toast stack */}
      <div className="upload-toast-stack" aria-live="polite" style={{ marginTop: toasts.length > 0 ? 8 : 0 }}>
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
    </>
  );
}
