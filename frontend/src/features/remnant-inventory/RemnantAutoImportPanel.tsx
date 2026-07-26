import { useEffect, useRef, useState } from 'react';
import { App, Alert, Button, Card, Form, Input, List, Space, Typography } from 'antd';
import { CloudUploadOutlined, FileSearchOutlined, FolderOpenOutlined, InboxOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { createAutoRemnantImportBatch, type AutoImportFile } from './api';
import { describeRemnantError } from './errors';
import type { RemnantImportBatch } from './types';
import type { TransferProgress } from '../../shared/api';
import { TransferProgressBar } from '../../shared/components';

interface Props {
  onCreated: (batch: RemnantImportBatch) => void;
}

interface WebkitEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
}

interface WebkitFileEntry extends WebkitEntry {
  file: (success: (file: File) => void, error?: (reason: unknown) => void) => void;
}

interface WebkitDirectoryReader {
  readEntries: (success: (entries: WebkitEntry[]) => void, error?: (reason: unknown) => void) => void;
}

interface WebkitDirectoryEntry extends WebkitEntry {
  createReader: () => WebkitDirectoryReader;
}

const drawingPattern = /\.(dwg|dxf)$/i;
const maximumFiles = 100;

function normalizePath(path: string): string {
  return path.split('\\').join('/').replace(/^\/+|\/+$/g, '');
}

function folderSelection(files: File[]): { entries: AutoImportFile[]; folderName?: string } {
  const firstPath = normalizePath(files.find((file) => file.webkitRelativePath)?.webkitRelativePath ?? '');
  const folderName = firstPath.includes('/') ? firstPath.split('/')[0] : undefined;
  return {
    folderName,
    entries: files.map((file) => {
      const fullPath = normalizePath(file.webkitRelativePath || file.name);
      const relativePath = folderName && fullPath.startsWith(`${folderName}/`)
        ? fullPath.slice(folderName.length + 1)
        : fullPath;
      return { file, relativePath };
    }),
  };
}

function readFileEntry(entry: WebkitFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function readDirectoryEntries(entry: WebkitDirectoryEntry): Promise<WebkitEntry[]> {
  const reader = entry.createReader();
  const collected: WebkitEntry[] = [];
  while (true) {
    const chunk = await new Promise<WebkitEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!chunk.length) return collected;
    collected.push(...chunk);
  }
}

async function collectEntry(entry: WebkitEntry, prefix = ''): Promise<AutoImportFile[]> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) return [{ file: await readFileEntry(entry as WebkitFileEntry), relativePath: path }];
  if (!entry.isDirectory) return [];
  const children = await readDirectoryEntries(entry as WebkitDirectoryEntry);
  return (await Promise.all(children.map((child) => collectEntry(child, path)))).flat();
}

export function RemnantAutoImportPanel({ onCreated }: Props) {
  const { message } = App.useApp();
  const drawingInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [entries, setEntries] = useState<AutoImportFile[]>([]);
  const [projectNo, setProjectNo] = useState('');
  const [folderName, setFolderName] = useState<string>();
  const [projectError, setProjectError] = useState('');
  const [notice, setNotice] = useState<string>();
  const [uploadProgress, setUploadProgress] = useState<TransferProgress | null>(null);

  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '');
    folderInput.current?.setAttribute('directory', '');
  }, []);

  const selectFiles = (files: File[], fromFolder: boolean, explicitFolder?: string) => {
    const selection = fromFolder ? folderSelection(files) : {
      entries: files.map((file) => ({ file, relativePath: file.name })),
      folderName: explicitFolder,
    };
    const accepted = selection.entries.filter(({ file }) => drawingPattern.test(file.name));
    const ignored = selection.entries.length - accepted.length;
    if (accepted.length > maximumFiles) {
      setNotice(`一次最多选择 ${maximumFiles} 张图纸，当前识别到 ${accepted.length} 张。`);
      return;
    }
    setEntries(accepted);
    setFolderName(selection.folderName);
    if (fromFolder && selection.folderName) {
      setProjectNo(selection.folderName);
      setProjectError('');
    }
    setNotice(ignored ? `已忽略 ${ignored} 个非 DWG/DXF 文件。` : undefined);
  };

  const create = useMutation({
    mutationFn: () => createAutoRemnantImportBatch(
      entries,
      projectNo.trim(),
      folderName,
      setUploadProgress,
    ),
    onSuccess: (batch) => {
      message.success(`已提交 ${batch.total_count} 张图纸自动解析`);
      onCreated(batch);
    },
    onError: (error) => message.error(describeRemnantError(error, '自动导入提交失败')),
  });

  const submit = () => {
    if (!projectNo.trim()) {
      setProjectError('请填写项目编号');
      return;
    }
    setProjectError('');
    create.mutate();
  };

  const dropFolder = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const items = [...event.dataTransfer.items];
    const roots: WebkitEntry[] = [];
    items.forEach((item) => {
      const entry = (item as DataTransferItem & { webkitGetAsEntry?: () => unknown }).webkitGetAsEntry?.() as WebkitEntry | null | undefined;
      if (entry) roots.push(entry);
    });
    if (!roots.length) {
      selectFiles([...event.dataTransfer.files], true);
      return;
    }
    const topFolder = roots.length === 1 && roots[0].isDirectory ? roots[0].name : undefined;
    const collected = (await Promise.all(roots.map((entry) => collectEntry(entry)))).flat();
    const normalized = collected.map(({ file, relativePath }) => ({
      file,
      relativePath: topFolder && relativePath.startsWith(`${topFolder}/`)
        ? relativePath.slice(topFolder.length + 1)
        : relativePath,
    }));
    selectFiles(
      normalized.map(({ file, relativePath }) => {
        Object.defineProperty(file, 'webkitRelativePath', {
          configurable: true,
          value: topFolder ? `${topFolder}/${relativePath}` : relativePath,
        });
        return file;
      }),
      true,
      topFolder,
    );
  };

  return (
    <Card bordered={false} className="remnant-import-card remnant-auto-import-card">
      <div className="remnant-section-heading">
        <div>
          <Typography.Title level={4}>自动解析余料图纸</Typography.Title>
          <Typography.Text type="secondary">仅收集 DWG/DXF，文件夹会递归读取并保留相对路径，单次最多 100 张。</Typography.Text>
        </div>
      </div>
      <div className="remnant-auto-entry-grid">
        <button type="button" className="remnant-auto-entry" onClick={() => drawingInput.current?.click()}>
          <FileSearchOutlined />
          <strong>选择图纸</strong>
          <span>从任意位置选择多张 DWG/DXF</span>
        </button>
        <div
          className="remnant-auto-entry"
          role="button"
          tabIndex={0}
          onClick={() => folderInput.current?.click()}
          onKeyDown={(event) => event.key === 'Enter' && folderInput.current?.click()}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => void dropFolder(event)}
        >
          <InboxOutlined />
          <strong>选择/拖入文件夹</strong>
          <span>递归读取子文件夹并预填项目编号</span>
        </div>
      </div>
      <input
        ref={drawingInput}
        hidden
        multiple
        type="file"
        accept=".dwg,.dxf"
        aria-label="选择图纸文件"
        onChange={(event) => selectFiles([...(event.target.files ?? [])], false)}
      />
      <input
        ref={folderInput}
        hidden
        multiple
        type="file"
        accept=".dwg,.dxf"
        aria-label="选择图纸文件夹"
        onChange={(event) => selectFiles([...(event.target.files ?? [])], true)}
      />
      {notice && <Alert showIcon type={notice.startsWith('一次最多') ? 'error' : 'info'} message={notice} style={{ marginTop: 16 }} />}
      <Form layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          label="项目编号"
          validateStatus={projectError ? 'error' : undefined}
          help={projectError || '提交前请确认，可修改文件夹名自动填入的项目编号。'}
        >
          <Input
            aria-label="项目编号"
            value={projectNo}
            maxLength={128}
            onChange={(event) => {
              setProjectNo(event.target.value);
              if (event.target.value.trim()) setProjectError('');
            }}
            placeholder="请输入项目编号"
          />
        </Form.Item>
      </Form>
      {entries.length > 0 && (
        <List
          size="small"
          className="remnant-auto-files"
          header={`已选择 ${entries.length} 张图纸${folderName ? ` · 顶层文件夹：${folderName}` : ''}`}
          dataSource={entries}
          renderItem={(entry) => <List.Item><Typography.Text ellipsis>{entry.relativePath}</Typography.Text></List.Item>}
        />
      )}
      <Space style={{ marginTop: 16 }}>
        <Button
          type="primary"
          icon={<CloudUploadOutlined />}
          disabled={!entries.length}
          loading={create.isPending}
          onClick={submit}
        >
          确认并开始解析
        </Button>
        <Typography.Text type="secondary"><FolderOpenOutlined /> 相对路径统一使用“/”分隔</Typography.Text>
      </Space>
      {uploadProgress && (
        <TransferProgressBar label="余料图纸文件夹上传" progress={uploadProgress} />
      )}
    </Card>
  );
}
