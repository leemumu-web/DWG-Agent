import { Button, Card, Checkbox, Popconfirm, Typography } from 'antd';
import { DeleteOutlined, DownloadOutlined, FolderOpenOutlined, FolderOutlined } from '@ant-design/icons';

import type { BatchInfo } from '../../../files';
import type { ConversionOperation } from './ConversionUploadPanel';

export interface ConversionFoldersPanelProps {
  batches: BatchInfo[];
  selectedBatchNames: string[];
  operation: ConversionOperation;
  selectedSourceCount: number;
  selectedPreview: string;
  selectedRemainder: number;
  onSelectionChange: (names: string[]) => void;
  onClearFileSelection: () => void;
  onToggle: (name: string) => void;
  onOpen: (name: string) => void;
  onDownload: () => void;
  onDelete: () => void;
}

export function ConversionFoldersPanel({
  batches,
  selectedBatchNames,
  operation,
  selectedSourceCount,
  selectedPreview,
  selectedRemainder,
  onSelectionChange,
  onClearFileSelection,
  onToggle,
  onOpen,
  onDownload,
  onDelete,
}: ConversionFoldersPanelProps) {
  return (
    <div className="folder-section">
      <div className="folder-heading">
        <Typography.Text strong style={{ fontSize: 14 }}>
          <FolderOpenOutlined style={{ marginRight: 6 }} />文件夹
          <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
            （上传文件夹时自动创建，勾选后可打包下载或删除）
          </Typography.Text>
        </Typography.Text>
      </div>
      <div className="folder-actions" aria-label="文件夹批量操作">
        <Typography.Text strong>
          {selectedBatchNames.length > 0 ? `已选 ${selectedBatchNames.length} 个文件夹` : `共 ${batches.length} 个文件夹`}
        </Typography.Text>
        {selectedBatchNames.length < batches.length && (
          <Button
            size="small"
            disabled={operation !== null}
            onClick={() => {
              onSelectionChange(batches.map((batch) => batch.name));
              onClearFileSelection();
            }}
          >
            全选 {batches.length} 个文件夹
          </Button>
        )}
        {selectedBatchNames.length > 0 && (
          <>
            <Button size="small" disabled={operation !== null} onClick={() => onSelectionChange([])}>清除选择</Button>
            <Button
              type="primary"
              size="small"
              icon={<DownloadOutlined />}
              loading={operation === 'batch-package'}
              disabled={operation !== null && operation !== 'batch-package'}
              onClick={onDownload}
            >
              打包下载 {selectedBatchNames.length} 个文件夹
            </Button>
            <Popconfirm
              title={`确认完整删除 ${selectedBatchNames.length} 个文件夹？`}
              description={(
                <div className="folder-delete-summary">
                  <div>将删除已知 {selectedSourceCount} 个源文件、它们的生成结果，并取消相关活动任务。</div>
                  <div>文件夹：{selectedPreview}{selectedRemainder > 0 ? ` 等 ${selectedBatchNames.length} 个` : ''}</div>
                  <div>删除为整体事务：全部成功或全部保留。</div>
                </div>
              )}
              onConfirm={onDelete}
              okText="确认删除"
              cancelText="取消"
              okButtonProps={{ danger: true, loading: operation === 'batch-delete' }}
              disabled={operation !== null}
            >
              <Button size="small" danger icon={<DeleteOutlined />} loading={operation === 'batch-delete'} disabled={operation !== null}>
                删除 {selectedBatchNames.length} 个文件夹
              </Button>
            </Popconfirm>
          </>
        )}
      </div>
      <div className="folder-grid">
        {batches.map((batch) => {
          const checked = selectedBatchNames.includes(batch.name);
          return (
            <Card
              key={batch.name}
              hoverable
              size="small"
              className={`folder-card${checked ? ' folder-card-selected' : ''}`}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <Checkbox
                  aria-label={`选择文件夹 ${batch.name}`}
                  checked={checked}
                  disabled={operation !== null}
                  onChange={() => onToggle(batch.name)}
                  onClick={(event) => event.stopPropagation()}
                />
                <button
                  type="button"
                  className="folder-open-button"
                  aria-label={`打开文件夹 ${batch.name}`}
                  disabled={operation !== null}
                  onClick={() => onOpen(batch.name)}
                >
                  <Card.Meta
                    avatar={<FolderOutlined style={{ fontSize: 24, color: '#faad14' }} />}
                    title={<Typography.Text ellipsis style={{ maxWidth: 120 }}>{batch.name}</Typography.Text>}
                    description={(
                      <div>
                        <div>{batch.file_count} 个文件</div>
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          {new Date(batch.latest_created_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </Typography.Text>
                      </div>
                    )}
                  />
                </button>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
