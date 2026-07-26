import { useEffect, useRef, useState } from 'react';
import {
  CheckCircleOutlined,
  CloudDownloadOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Checkbox,
  Input,
  Modal,
  Space,
  Spin,
  Steps,
  Tag,
  Typography,
} from 'antd';

import { describeApiError, operatorErrorMessage, type TransferProgress } from '../../shared/api';
import {
  ApiErrorAlert,
  fmtDateTime,
  fmtSize,
  TransferProgressBar,
} from '../../shared/components';
import type { WorkflowRetentionExport } from './workflow';
import {
  createWorkflowRetentionExport,
  getLatestWorkflowRetentionExport,
  getWorkflowRetentionExport,
  getWorkflowRetentionPreview,
  queueWorkflowRetentionPurge,
  downloadWorkflowRetentionExport,
} from './workflows.api';

const POLLED_STATUSES = new Set(['prepared', 'downloading', 'purge_queued', 'purging']);

function statusStep(row: WorkflowRetentionExport | null | undefined): number {
  if (!row) return 0;
  if (['prepared', 'downloading', 'download_failed'].includes(row.status)) return 1;
  return 2;
}

export function WorkflowRetentionControl({
  workflowId,
  isAdmin,
  onPurged,
}: {
  workflowId: number;
  isAdmin: boolean;
  onPurged: () => void;
}) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<WorkflowRetentionExport | null>(null);
  const [backupChecked, setBackupChecked] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [downloadProgress, setDownloadProgress] = useState<TransferProgress | null>(null);
  const purgedNoticeRef = useRef<string | null>(null);

  const previewQ = useQuery({
    queryKey: ['workflow-retention-preview', workflowId],
    queryFn: () => getWorkflowRetentionPreview(workflowId),
    enabled: open,
    retry: false,
  });
  const latestQ = useQuery({
    queryKey: ['workflow-retention-latest', workflowId],
    queryFn: () => getLatestWorkflowRetentionExport(workflowId),
    enabled: open,
    retry: false,
  });
  const exportUid = created?.export_uid ?? latestQ.data?.export_uid;
  const statusQ = useQuery({
    queryKey: ['workflow-retention-export', workflowId, exportUid],
    queryFn: () => getWorkflowRetentionExport(workflowId, exportUid!),
    enabled: open && Boolean(exportUid),
    refetchInterval: (query) => (
      POLLED_STATUSES.has(query.state.data?.status ?? '') ? 1_500 : false
    ),
    retry: false,
  });
  const exportRow = statusQ.data ?? created ?? latestQ.data;
  const expectedConfirmation = `DELETE WORKFLOW ${workflowId}`;

  const createM = useMutation({
    mutationFn: () => createWorkflowRetentionExport(workflowId),
    onSuccess: (next) => {
      setCreated(next);
      setBackupChecked(false);
      setConfirmation('');
      queryClient.setQueryData(
        ['workflow-retention-export', workflowId, next.export_uid],
        next,
      );
      queryClient.setQueryData(['workflow-retention-latest', workflowId], next);
      message.success('完整备份清单已锁定，可以开始下载');
    },
  });

  const purgeM = useMutation({
    mutationFn: () => queueWorkflowRetentionPurge(
      workflowId,
      exportRow!.export_uid,
      confirmation,
    ),
    onSuccess: (next) => {
      queryClient.setQueryData(
        ['workflow-retention-export', workflowId, next.export_uid],
        next,
      );
      setCreated(next);
      message.info('永久清理已进入维护队列，可以关闭窗口后稍后回来查看');
    },
  });
  const downloadM = useMutation({
    mutationFn: (row: WorkflowRetentionExport) => (
      downloadWorkflowRetentionExport(row, setDownloadProgress)
    ),
    onSuccess: () => {
      message.success('完整备份已下载到浏览器');
      setTimeout(() => { void statusQ.refetch(); }, 300);
    },
    onError: (error) => {
      message.error(describeApiError(error, '完整备份下载未能完成'));
      void statusQ.refetch();
    },
  });

  useEffect(() => {
    if (!exportRow || exportRow.status !== 'purged') return;
    if (purgedNoticeRef.current === exportRow.export_uid) return;
    purgedNoticeRef.current = exportRow.export_uid;
    message.success(`整批清理完成，服务器已释放 ${fmtSize(exportRow.purged_size_bytes)}`);
    void queryClient.invalidateQueries({
      queryKey: ['workflow-retention-preview', workflowId],
    });
    onPurged();
  }, [exportRow, message, onPurged, queryClient, workflowId]);

  const startDownload = () => {
    if (!exportRow) return;
    setDownloadProgress(null);
    downloadM.mutate(exportRow);
  };

  const close = () => {
    if (downloadM.isPending || purgeM.isPending) return;
    setOpen(false);
    setBackupChecked(false);
    setConfirmation('');
  };
  const busy = purgeM.isPending || ['purge_queued', 'purging'].includes(exportRow?.status ?? '');
  const canPurge = Boolean(
    isAdmin
    && exportRow
    && ['downloaded', 'purge_failed'].includes(exportRow.status)
    && backupChecked
    && confirmation === expectedConfirmation,
  );

  return (
    <>
      <Button
        icon={<SafetyCertificateOutlined />}
        onClick={() => setOpen(true)}
      >
        完整备份与释放空间
      </Button>
      <Modal
        open={open}
        title={`生产批次 #${workflowId} · 完整备份与释放空间`}
        width={760}
        maskClosable={false}
        closable={!purgeM.isPending && !downloadM.isPending}
        onCancel={close}
        footer={<Button onClick={close} disabled={purgeM.isPending || downloadM.isPending}>关闭</Button>}
      >
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="warning"
            showIcon
            message="先完整备份，再永久释放服务器空间"
            description="流程、任务、文件名、大小和哈希记录会保留；对象字节和 DXF 预览缓存删除后无法从服务器恢复。"
          />
          <Steps
            size="small"
            current={statusStep(exportRow)}
            items={[
              { title: '核对范围' },
              { title: '下载完整备份' },
              { title: '确认并清理' },
            ]}
          />

          {(previewQ.isLoading || latestQ.isLoading) && (
            <Spin tip="正在核对完整生产关系与存储登记" />
          )}
          {previewQ.isError && (
            <ApiErrorAlert
              title="完整备份范围检查失败"
              error={previewQ.error}
              fallback="无法核对本批服务器文件"
              retryLabel="重新检查"
              retryLoading={previewQ.isFetching}
              onRetry={() => previewQ.refetch()}
            />
          )}
          {latestQ.isError && (
            <ApiErrorAlert
              title="上次备份状态读取失败"
              error={latestQ.error}
              fallback="无法恢复上次完整备份状态"
              retryLabel="重新读取"
              retryLoading={latestQ.isFetching}
              onRetry={() => latestQ.refetch()}
            />
          )}

          {previewQ.data && (
            <div className="workflow-retention-summary">
              <div><small>正式文件</small><strong>{previewQ.data.file_count} 个</strong></div>
              <div><small>预览缓存</small><strong>{previewQ.data.preview_cache_count} 个</strong></div>
              <div><small>预计释放</small><strong>{fmtSize(previewQ.data.reclaimable_size_bytes)}</strong></div>
            </div>
          )}
          {previewQ.data?.blocked && (
            <Alert
              type="error"
              showIcon
              message="当前批次不能永久清理"
              description={(
                <Space orientation="vertical" size={4}>
                  {previewQ.data.blockers.map((blocker) => (
                    <Typography.Text key={blocker.code}>
                      {operatorErrorMessage(
                        blocker.code,
                        blocker.message,
                        '当前生产文件不满足清理条件，请先完成下载和状态核对。',
                      )}
                    </Typography.Text>
                  ))}
                </Space>
              )}
            />
          )}

          {!exportRow && previewQ.data && !previewQ.data.blocked && (
            <Alert
              type="info"
              showIcon
              message="第 1 步：锁定完整备份清单"
              description="服务器会逐个核对文件是否存在、大小是否与系统登记一致；核对通过后才生成下载入口。"
              action={(
                <Button
                  type="primary"
                  icon={<CloudDownloadOutlined />}
                  loading={createM.isPending}
                  onClick={() => createM.mutate()}
                >
                  生成完整备份
                </Button>
              )}
            />
          )}
          {createM.isError && (
            <ApiErrorAlert
              title="完整备份生成失败"
              error={createM.error}
              fallback="对象核对未通过，完整备份没有生成"
              retryLabel="重新预检并生成"
              retryLoading={createM.isPending}
              onRetry={() => createM.mutate()}
            />
          )}
          {statusQ.isError && (
            <ApiErrorAlert
              title="完整备份状态读取失败"
              error={statusQ.error}
              fallback="无法确认备份或清理进度"
              retryLabel="刷新状态"
              retryLoading={statusQ.isFetching}
              onRetry={() => statusQ.refetch()}
            />
          )}

          {exportRow && ['prepared', 'downloading', 'download_failed'].includes(exportRow.status) && (
            <Space orientation="vertical" size={10} style={{ width: '100%' }}>
              <Alert
                type={exportRow.status === 'download_failed' ? 'error' : 'info'}
                showIcon
                message={exportRow.status === 'downloading'
                  ? '第 2 步：完整备份正在传输'
                  : exportRow.status === 'download_failed'
                    ? '完整备份没有完整传输，服务器文件仍全部保留'
                    : '第 2 步：下载完整备份'}
                description={`${exportRow.filename} · ${exportRow.file_count} 个文件 · ${fmtSize(exportRow.source_size_bytes)}`}
                action={(
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={exportRow.status === 'download_failed' ? <ReloadOutlined /> : <CloudDownloadOutlined />}
                      loading={downloadM.isPending}
                      disabled={downloadM.isPending || exportRow.status === 'downloading' || !exportRow.download_url}
                      onClick={startDownload}
                    >
                      {exportRow.status === 'download_failed' ? '重新下载' : '下载完整备份'}
                    </Button>
                    {exportRow.status !== 'downloading' && (
                      <Button loading={createM.isPending} disabled={downloadM.isPending} onClick={() => createM.mutate()}>
                        重新生成凭据
                      </Button>
                    )}
                  </Space>
                )}
              />
              {downloadProgress && (
                <TransferProgressBar label="完整备份下载" progress={downloadProgress} />
              )}
            </Space>
          )}

          {exportRow && ['downloaded', 'purge_failed'].includes(exportRow.status) && (
            <Space orientation="vertical" size={12} className="workflow-retention-confirmation">
              <Alert
                type={exportRow.status === 'purge_failed' ? 'error' : 'success'}
                showIcon
                icon={exportRow.status === 'downloaded' ? <CheckCircleOutlined /> : undefined}
                message={exportRow.status === 'downloaded'
                  ? '服务端已确认完整备份发送完毕'
                  : '上次永久清理未完成，生产关系仍保留，可安全重试'}
                description={exportRow.error_message
                  ? operatorErrorMessage(
                    exportRow.error_code,
                    exportRow.error_message,
                    '上次服务器空间清理未完成，生产文件仍按当前状态保留。',
                  )
                  : `发送完成时间：${fmtDateTime(exportRow.downloaded_at)}`}
              />
              {!isAdmin ? (
                <Alert
                  type="info"
                  showIcon
                  message="完整备份已完成，请联系管理员释放服务器空间"
                  description="只有管理员可以执行不可恢复的物理删除。"
                />
              ) : (
                <>
                  <Checkbox
                    checked={backupChecked}
                    onChange={(event) => setBackupChecked(event.target.checked)}
                  >
                    我已在本地打开 ZIP，并确认所需文件可以读取
                  </Checkbox>
                  <Typography.Text>
                    输入确认词 <Typography.Text code copyable>{expectedConfirmation}</Typography.Text>
                  </Typography.Text>
                  <Input
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    placeholder={expectedConfirmation}
                    status={confirmation && confirmation !== expectedConfirmation ? 'error' : undefined}
                    autoComplete="off"
                  />
                  <Button
                    danger
                    type="primary"
                    icon={<DeleteOutlined />}
                    loading={purgeM.isPending}
                    disabled={!canPurge}
                    onClick={() => purgeM.mutate()}
                  >
                    永久删除本批服务器文件
                  </Button>
                </>
              )}
            </Space>
          )}
          {purgeM.isError && (
            <ApiErrorAlert
              title="永久清理未能入队"
              error={purgeM.error}
              fallback="清理没有开始，服务器文件仍保留"
              retryLabel="重新提交"
              retryLoading={purgeM.isPending}
              onRetry={() => purgeM.mutate()}
            />
          )}
          {exportRow && ['purge_queued', 'purging'].includes(exportRow.status) && (
            <Alert
              type="info"
              showIcon
              message={exportRow.status === 'purge_queued' ? '永久清理已排队' : '正在永久清理服务器对象'}
              description="可以关闭窗口；后台会保留清理流水，重新打开后可继续查看。请勿重复提交。"
              action={<Tag color="processing">{exportRow.task_id ?? '维护任务'}</Tag>}
            />
          )}
          {exportRow?.status === 'purged' && (
            <Alert
              type="success"
              showIcon
              message="本批服务器文件已永久清理"
              description={`共清理 ${exportRow.purged_file_count} 个对象，释放 ${fmtSize(exportRow.purged_size_bytes)}；生产关系和审计流水仍保留。`}
            />
          )}
          {exportRow && (
            <Typography.Text type="secondary">
              备份状态更新于 {fmtDateTime(exportRow.updated_at)}
              {busy ? ' · 后台任务处理中' : ''}
            </Typography.Text>
          )}
        </Space>
      </Modal>
    </>
  );
}
